"""A task-bound delegate to existing LLMService, with conservative same-target fallback.

No tool is executed here. After a returned tool plan or a stream delta the attempt is
committed: this delegate cannot retry it. Knowledge/vision grants bind config.target;
all fallback candidates preserve that target even for ordinary text requests.
"""

import asyncio
import json
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from contextlib import contextmanager
from contextvars import ContextVar
import time
from uuid import uuid4

from app.services.llm.openai_provider import LLMHTTPError, LLMTimeoutError, LLMConnectionError
from app.services.llm.routing.contracts import RoutingError
from app.services.llm.routing.observation import RequestObservation, observe_request
from app.services.llm.routing.selection import eligibility

_scope = ContextVar("model_routing_scope", default=None)


@contextmanager
def routing_scope(task_id, workflow_id=None):
    token = _scope.set((task_id, workflow_id))
    try:
        yield
    finally:
        _scope.reset(token)


def transient(error):
    return (
        isinstance(error, (LLMTimeoutError, LLMConnectionError))
        or isinstance(error, LLMHTTPError)
        and error.status_code in {408, 429, 500, 502, 503, 504}
    )


def tool_schema_valid(result, tools):
    if not isinstance(result, dict):
        return False
    calls = result.get("tool_calls")
    if calls is None:
        return isinstance(result.get("content"), str)
    if not isinstance(calls, list) or len(calls) > 32:
        return False
    schemas = {row["function"]["name"]: row["function"].get("parameters", {}) for row in tools}
    try:
        identities = set()
        for call in calls:
            function = call["function"]
            identity = call["id"]
            if (
                not isinstance(identity, str)
                or not 1 <= len(identity) <= 200
                or identity in identities
                or call.get("type") != "function"
            ):
                return False
            identities.add(identity)
            name, raw = function["name"], function["arguments"]
            if name not in schemas or not isinstance(raw, str) or len(raw) > 200000:
                return False
            arguments = json.loads(raw)
            if not isinstance(arguments, dict):
                return False
            Draft202012Validator(schemas[name], registry=Registry()).validate(arguments)
    except (KeyError, TypeError, ValueError, ValidationError, Unresolvable):
        return False
    return True


async def join_owned(task):
    """Repeated cancellation cannot detach an owned SQLite operation."""
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancelled = error
    try:
        value = task.result()
    except Exception:
        if cancelled:
            raise cancelled
        raise
    return value, cancelled


class RoutedService:
    def __init__(self, entry, decision, registry, policy, requirements, metrics, ledger):
        self.entry, self.decision, self.registry = entry, decision, registry
        self.policy, self.requirements, self.metrics, self.ledger = (
            policy,
            requirements,
            metrics,
            ledger,
        )
        self.config = entry.service.config
        self.last_error = False
        self.availability_checked = False
        self.observed_capabilities = entry.service.observed_capabilities
        self.task_id, self.workflow_id = _scope.get() or (str(uuid4()), None)

    def _attempts(self):
        yield self.entry
        if self.policy.mode == "MANUAL" or not self.policy.fallback_enabled:
            return
        # At most one fallback, authorized independently and held to the same
        # model target already checked by attachment, knowledge and vision code.
        for entry in self.registry.entries:
            if (
                entry.record.model_id == self.entry.record.model_id
                or entry.target != self.entry.target
            ):
                continue
            if not entry.record.allow_auto or (
                not entry.local and not entry.record.allow_remote_text
            ):
                continue
            if eligibility(entry, self.requirements, self.policy, self.metrics) is None:
                yield entry
                return

    async def _start(self, entry):
        if entry.record.model_id != self.entry.record.model_id:
            from app.services.llm.routing.trace import emit

            emit("MODEL_ROUTING_FALLBACK", self.decision, entry)
        reservation = asyncio.create_task(
            asyncio.to_thread(
                self.ledger.reserve,
                entry,
                self.policy,
                self.task_id,
                self.workflow_id,
                decision=self.decision.public()
                | {
                    "attempt_model_id": entry.record.model_id,
                    "is_fallback": entry.record.model_id != self.entry.record.model_id,
                },
            )
        )
        request_id, cancelled = await join_owned(reservation)
        if cancelled:
            cleanup = asyncio.create_task(
                asyncio.to_thread(self.ledger.finish, request_id, "CANCELLED_NOT_SENT")
            )
            try:
                await join_owned(cleanup)
            finally:
                raise cancelled
        return request_id, time.perf_counter(), RequestObservation(self.policy.max_output_tokens)

    async def _finish(
        self, entry, request_id, started, observer, error=None, first=None, tool_valid=None
    ):
        self.availability_checked = True
        self.last_error = error is not None
        status = (
            "CANCELLED_UNKNOWN"
            if isinstance(error, (asyncio.CancelledError, GeneratorExit))
            else "FAILED_UNKNOWN"
            if error
            else "COMPLETED"
        )
        finalization = asyncio.create_task(
            asyncio.to_thread(self.ledger.finish, request_id, status, observer.usage)
        )
        _, cleanup_cancelled = await join_owned(finalization)
        if not isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            self.metrics.record(
                entry.version,
                success=error is None,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                ttft_ms=first,
                transient=transient(error),
                tool_schema_valid=tool_valid,
                task_kind=self.requirements.task,
                transport_success=(200 <= observer.http_status < 300)
                if observer.http_status is not None
                else False,
            )
        if cleanup_cancelled:
            raise cleanup_cancelled

    async def _call(self, operation, messages, tools=None):
        last = None
        for entry in self._attempts():
            request_id, started, observer = await self._start(entry)
            error, valid = None, None
            try:
                with observe_request(observer):
                    result = (
                        await entry.service.plan_tools(messages, tools)
                        if operation == "plan_tools"
                        else await entry.service.chat(messages)
                    )
                if operation == "plan_tools":
                    valid = tool_schema_valid(result, tools)
                    if not valid:
                        raise RoutingError("TOOL_SCHEMA_INVALID")
                    valid = True if result.get("tool_calls") else None
                    if result.get("tool_calls"):
                        entry.service.observed_capabilities["tool_calling"] = True
                return result
            except BaseException as exc:
                error, last = exc, exc
                if not transient(exc):
                    raise
            finally:
                await self._finish(entry, request_id, started, observer, error, tool_valid=valid)
        raise last or RoutingError("NO_FALLBACK_MODEL")

    async def chat(self, messages):
        return await self._call("chat", messages)

    async def plan_tools(self, messages, tools):
        return await self._call("plan_tools", messages, tools)

    async def stream_chat(self, messages):
        last = None
        for entry in self._attempts():
            request_id, started, observer = await self._start(entry)
            error, first, emitted = None, None, False
            try:
                iterator = entry.service.stream_chat(messages).__aiter__()
                try:
                    while True:
                        with observe_request(observer):
                            try:
                                delta = await anext(iterator)
                            except StopAsyncIteration:
                                break
                        if delta:
                            emitted = True
                            if first is None:
                                first = (time.perf_counter() - started) * 1000
                            # Never retain a request-local observer while the caller
                            # is handling a yielded delta or making another request.
                            yield delta
                finally:
                    with observe_request(observer):
                        await iterator.aclose()
                return
            except BaseException as exc:
                error, last = exc, exc
                if emitted or not transient(exc):
                    raise
            finally:
                await self._finish(entry, request_id, started, observer, error, first)
        raise last or RoutingError("NO_FALLBACK_MODEL")

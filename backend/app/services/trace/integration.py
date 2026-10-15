"""Small optional wrappers around real planner/model operations."""

from functools import wraps
import inspect
from uuid import uuid4

from app.services.trace.scope import current_span, current_trace, span_scope


def traced_planning(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        bound = signature.bind(*args, **kwargs).arguments
        recorder = getattr(bound["app"].state, "trace_recorder", None)
        if recorder is None:
            return await function(*args, **kwargs)
        try:
            context = recorder.create(str(uuid4()), bound.get("conversation_id"))
        except Exception:
            return await function(*args, **kwargs)
        token = current_trace.set(context)
        parent = current_span.set(context.root_span_id)
        try:
            with span_scope("planning", "TASK_PLANNING", "PLANNING_COMPLETED") as span:
                result = await function(*args, **kwargs)
                context.bind(result.get("conversation_id"), result.get("work_session_id"))
                span.metadata.update(
                    step_count=len(result.get("calls", [])),
                    tools_requested=bool(result.get("calls")),
                )
            context.send("state", status="waiting_permission" if context.permission else "ready")
            return result
        except BaseException as error:
            context.finish(error)
            raise
        finally:
            current_span.reset(parent)
            current_trace.reset(token)

    return wrapped


def traced_model(operation):
    def decorate(function):
        @wraps(function)
        async def wrapped(self, *args, **kwargs):
            with span_scope(
                "model",
                "MODEL_REQUEST_STARTED",
                "MODEL_REQUEST_COMPLETED",
                model_name=getattr(self.config, "model", None),
                metadata={
                    "operation": operation,
                    "streaming": False,
                    "tools_requested": operation == "plan_tools",
                },
            ):
                return await function(self, *args, **kwargs)

        return wrapped

    return decorate


def traced_stream(function):
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        # Explicit nonstreaming fallback calls the instrumented chat method once.
        if self.config and self.config.capabilities.get("streaming") is False:
            async for delta in function(self, *args, **kwargs):
                yield delta
            return
        with span_scope(
            "model",
            "MODEL_REQUEST_STARTED",
            "MODEL_REQUEST_COMPLETED",
            model_name=getattr(self.config, "model", None),
            metadata={"operation": "stream_chat", "streaming": True},
        ):
            async for delta in function(self, *args, **kwargs):
                yield delta

    return wrapped


def traced_verification(function):
    @wraps(function)
    def wrapped(self, goal, details):
        tools = [row.get("name", "") for row in details]
        goal_kind = next(
            (
                kind
                for prefix, kind in [
                    ("coding.", "coding"),
                    ("music.", "music"),
                    ("app.", "application"),
                    ("browser.", "browser"),
                    ("code.", "file"),
                    ("filesystem.", "file"),
                    ("workspace.", "file"),
                ]
                if any(name.startswith(prefix) for name in tools)
            ),
            "general",
        )
        with span_scope(
            "verification",
            "VERIFICATION_STARTED",
            "VERIFICATION_PASSED",
            metadata={
                "verification_method": "deterministic_result_rules",
                "step_count": len(details),
                "goal_kind": goal_kind,
            },
        ) as span:
            result = function(self, goal, details)
            span.result({"verified": result})
            available = any(item.get("result", {}).get("verified") is True for item in details)
            span.metadata["evidence_available"] = available
            span.verification_status = (
                "passed" if result and available else "rule_passed" if result else "failed"
            )
            return result

    return wrapped

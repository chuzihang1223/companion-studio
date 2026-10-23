"""Routing benchmark in the original isolated worker; evidence describes its actual boundary."""

import json
import time
from dataclasses import replace
from uuid import uuid4

import httpx
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.session import Base
from app.services.evaluation.router_fixtures import RouterFixture, blocked, blocked_async
from app.services.llm.routing.budget import BudgetLedger
from app.services.llm.routing.contracts import ModelRecord, Requirements, TaskCapabilityResolver
from app.services.llm.routing.service import routing_scope


async def component(index, f):
    if index == 4:
        try:
            ModelRecord(model_id="bad", display_name="bad", input_cost=0)
        except ValidationError:
            return {"invalid_price_rejected": True, "zero_http_calls": not f.calls}
        return {"invalid_price_rejected": False}
    f.add()
    state = f.state()
    if index == 1:
        public = json.dumps(state.public())
        return {
            "registered_once": len(state.registry.entries) == 1,
            "no_credential_or_endpoint": "api_key" not in public and "127.0.0.1" not in public,
        }
    if index in {2, 3, 9, 17}:
        record, service = f.models[0]
        if index == 2:
            f.models[0] = (record.model_copy(update={"enabled": False}), service)
        else:
            service.config.capabilities["tool_calling"] = None if index == 3 else False
        f.add("two")
        state = f.state()
        selected = state.service("planning_model", tools=True)
        await selected.plan_tools(
            [],
            [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        )
        return {
            "incompatible_not_called": not f.calls["one"],
            "eligible_called_once": len(f.calls["two"]) == 1,
        }
    if index == 5:
        old = f.app.state.model_roles.capture()
        selected = old.service("character_model")
        f.models.clear()
        f.add(api_model="changed")
        f.state()
        return {
            "version_changed": selected.entry.version
            != f.app.state.model_roles.service("character_model").entry.version,
            "old_service_immutable": selected.config.model == "synthetic",
        }
    if index == 6:
        record = state.registry.entries[0].record
        return {
            "price_unknown": record.price_source == "UNKNOWN"
            and record.input_cost is None
            and record.output_cost is None
        }
    if index in {7, 8, 10, 11, 12}:
        task = {7: "CHAT", 8: "PLANNING", 10: "CODING", 11: "RAG", 12: "VISION"}[index]
        if index == 12:
            f.models[0][1].config.capabilities["vision"] = True
        service = state.service(
            "character_model", requirements=TaskCapabilityResolver.resolve(task)
        )
        if index in {8, 10}:
            await service.plan_tools(
                [],
                [
                    {
                        "type": "function",
                        "function": {"name": "read", "parameters": {"type": "object"}},
                    }
                ],
            )
        else:
            await service.chat([{"role": "user", "content": "Synthetic input"}])
        return {
            "actual_provider_called_once": len(f.calls["one"]) == 1,
            "task_kind_preserved": service.requirements.task == task,
        }
    if index in {13, 14}:
        tasks = ["EMBEDDING"] if index == 13 else ["ASR", "TTS"]
        return {
            "typed_provider_required": all(
                blocked(
                    lambda task=task: state.service(
                        "character_model", requirements=TaskCapabilityResolver.resolve(task)
                    )
                )
                for task in tasks
            ),
            "no_chat_request": not f.calls["one"],
        }
    if index == 15:
        f.models.clear()
        f.calls.clear()
        f.add(responder=lambda _: httpx.Response(503))
        f.add("two")
        state = f.state(mode="MANUAL", manual_model="one", fallback_enabled=True)
        try:
            await state.service("character_model").chat([])
        except Exception:
            return {
                "manual_failed_without_fallback": len(f.calls["one"]) == 1 and not f.calls["two"]
            }
        return {"manual_failed_without_fallback": False}
    if index == 16:
        f.add("two", priority=10)
        state = replace(f.state(mode="ROLE_BASED"), roles={"coding_model": "two"})
        await state.service("coding_model", tools=True).plan_tools([], [])
        return {"role_binding_used": len(f.calls["two"]) == 1 and not f.calls["one"]}
    if index == 18:
        f.add("known", price_source="USER", input_cost=1, output_cost=1)
        state = f.state(preference="LOW_COST")
        return {
            "unknown_not_zero": state.service("character_model").entry.record.model_id == "known"
        }
    if index == 19:
        f.add("fast")
        state = f.state(preference="LOW_LATENCY")
        for entry, duration in zip(state.registry.entries, (50, 10)):
            f.metrics.record(entry.version, success=True, elapsed_ms=duration)
        return {
            "observed_latency_used": state.service("character_model").entry.record.model_id
            == "fast",
            "sample_count_exact": f.metrics.public(state.registry.entries[1].version)["total"]["n"]
            == 1,
        }
    if index == 20:
        f.add(
            "cloud",
            endpoint="https://synthetic.example.invalid/v1",
            priority=10,
            allow_remote_text=True,
        )
        state = f.state()
        service = state.service("character_model", requirements=Requirements(privacy="LOCAL_ONLY"))
        await service.chat([])
        return {"local_only": service.entry.local and not f.calls["cloud"]}
    if index == 21:
        f.add("priority", priority=9)
        return {
            "explicit_priority_used": f.state(preference="QUALITY_FIRST")
            .service("character_model")
            .entry.record.model_id
            == "priority"
        }
    if index == 22:
        f.models[0][1].config.capabilities["text"] = None
        return {
            "blocked_without_candidate": blocked(lambda: f.state().service("character_model")),
            "zero_http_calls": not f.calls["one"],
        }
    if index in {23, 24}:
        if index == 24:
            f.models.clear()
            f.add(
                responder=lambda _: httpx.Response(
                    200, json={"choices": [{"message": {"content": "no usage"}}]}
                ),
                price_source="USER",
                input_cost=1,
                output_cost=1,
            )
            state = f.state()
        await state.service("character_model").chat([])
        row = f.ledger.recent()[0]
        return {
            "usage_matches_boundary": row["usage"]["total_tokens"] == 7
            and row["usage"]["cached_tokens"] == 1
            if index == 23
            else row["usage"] is None and row["cost_status"] == "USER_ESTIMATED"
        }
    if index in {25, 29}:
        if index == 29:
            record, service = f.models[0]
            f.models[0] = (
                record.model_copy(
                    update={"price_source": "USER", "input_cost": 0, "output_cost": 0}
                ),
                service,
            )
        state = f.state(max_cost_per_request=1)
        return {
            "money_limit_fail_closed": blocked(lambda: state.service("character_model")),
            "zero_http_calls": not f.calls["one"],
        }
    if index in {26, 27}:
        patch = {"max_requests_per_task" if index == 26 else "max_requests_per_workflow": 1}
        with routing_scope("same-task", "same-workflow"):
            service = f.state(**patch).service("character_model")
            await service.chat([])
            denied = await blocked_async(lambda: service.chat([]))
        return {
            "second_request_blocked": denied,
            "one_actual_http_request": len(f.calls["one"]) == 1,
        }
    if index == 28:
        before = f.app.state.model_roles.identity()
        f.state(preference="LOW_COST")
        changed = f.app.state.model_roles.identity() != before
        same = f.app.state.model_roles.identity()
        f.state(preference="LOW_COST")
        return {
            "policy_changes_dependency_identity": changed,
            "restart_identity_deterministic": f.app.state.model_roles.identity() == same,
        }
    if index in {30, 31, 32, 33, 34, 35}:

        def fail(request):
            if index == 31:
                raise httpx.ConnectError("synthetic", request=request)
            if index == 33:
                raise httpx.ReadTimeout("synthetic", request=request)
            return httpx.Response(429 if index == 32 else 503)

        f.models.clear()
        f.calls.clear()
        f.add(responder=fail, priority=10)
        if index == 34:
            f.add("bad", caps={"text": True, "tool_calling": False})
        f.add(
            "backup",
            endpoint="https://different.example.invalid/v1"
            if index == 35
            else "http://127.0.0.1:9/v1",
            allow_remote_text=True,
        )
        state = f.state(fallback_enabled=True)
        if index == 35:
            try:
                await state.service("character_model").chat([])
            except Exception:
                return {
                    "cross_target_blocked": not f.calls["backup"],
                    "primary_only": len(f.calls["one"]) == 1,
                }
            return {"cross_target_blocked": False}
        if index == 34:
            await state.service("planning_model", tools=True).plan_tools([], [])
        else:
            await state.service("character_model").chat([])
        return {
            "one_fallback_only": len(f.calls["one"]) == len(f.calls["backup"]) == 1,
            "both_reserved": len(f.ledger.recent()) == 2,
            "invalid_backup_not_called": not f.calls.get("bad"),
        }
    if index == 36:
        f.models.clear()
        f.calls.clear()
        answer = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "one-call",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        }
        f.add(
            responder=lambda _: httpx.Response(200, json={"choices": [{"message": answer}]}),
            priority=10,
        )
        f.add("backup")
        service = f.state(fallback_enabled=True).service("planning_model", tools=True)
        result = await service.plan_tools(
            [],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "parameters": {"type": "object", "additionalProperties": False},
                    },
                }
            ],
        )
        return {
            "plan_returned_once": len(result["tool_calls"]) == 1 and len(f.calls["one"]) == 1,
            "no_replay": not f.calls["backup"],
        }
    return None


async def run_case(app, trace_store, case, payload, workspace):
    started = time.perf_counter()
    engine = create_engine(
        f"sqlite:///{(workspace / 'router-ledger.sqlite').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    fixture = RouterFixture(app, BudgetLedger(sessionmaker(engine)))
    error = None
    try:
        index = int(case.case_id[-3:])
        if index <= 36:
            checks = await component(index, fixture)
        else:
            from app.services.evaluation.router_integration_cases import run

            fixture.add()
            fixture.state()
            checks = await run(index, fixture, trace_store, workspace)
    except Exception as failure:
        checks, error = {"uncaught_fixture_error": False}, type(failure).__name__
    finally:
        attempts = fixture.ledger.recent()
        engine.dispose()
    from sqlalchemy import select
    from app.models.agent_trace import AgentTrace
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        traces = list(db.scalars(select(AgentTrace)))
        links = next(
            (
                {
                    "trace_id": row.id,
                    "task_id": row.task_id,
                    "conversation_id": row.conversation_id,
                    "work_session_id": row.work_session_id,
                }
                for row in reversed(traces)
                if row.conversation_id
            ),
            {},
        )
    blocked_case = checks is None
    passed = bool(checks) and all(value is True for value in checks.values())
    evidence = {
        "checks": checks or {},
        "boundary": "production_router_service_sqlite_http_mock",
        "external_requests": 0,
        "actual_provider_quality": False,
        "actual_provider_billing": False,
    }
    result = {
        "case_id": case.case_id,
        "completion_status": "BLOCKED" if blocked_case else "PASS" if passed else "FAIL",
        "passed": passed,
        "score": int(passed),
        "grader_type": "model_router",
        "error_code": "ROUTER_INTEGRATION_REQUIRED"
        if blocked_case
        else "ROUTER_ASSERTION_FAILED"
        if not passed
        else None,
        "duration_ms": (time.perf_counter() - started) * 1000,
        "tool_calls": fixture.observation.get("tool_calls", 0),
        "model_requests": sum(len(calls) for calls in fixture.calls.values()),
        "retry_count": sum(
            bool((row.get("decision") or {}).get("is_fallback")) for row in attempts
        ),
        "recovery_succeeded": False,
        "model_usage": {"status": "HTTP_MOCK"},
        "evidence": evidence,
        "evidence_refs": ["evidence:checks"],
        "trace_count": len(traces),
        "expected_failure": False,
        "dimensions": {"task_success": passed},
        "explanation": "Component assertions use the original routing/provider chain; live model quality and billing are not measured.",
        "fixture_error_code": error,
    }
    return {"result": result, "links": links, "budget": {"requests": 0, "total_tokens": None}}

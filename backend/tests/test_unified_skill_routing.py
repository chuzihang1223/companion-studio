"""INT-009..016: production orchestration with explicitly synthetic provider/audio boundaries."""

import asyncio
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from unified_skill_fixture import unified_host
from unified_skill_scenarios import chain, report_graph, scheduled_run, complete_schedule
from workflow_fixture import start, settle
from app.database.session import SessionLocal
from app.models import Conversation, Message, ManualMemory
from app.services.workflows.contracts import Node
from app.services.llm.routing.budget import BudgetLedger
from app.services.llm.routing.contracts import RoutingError


def test_INT_009_actual_agent_planner_filters_text_only_provider(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            h.fixture.models.clear()
            h.fixture.add("text", caps={"text": True}, priority=10)
            h.fixture.add("tools", responder=h.respond)
            h.fixture.state()
            plan, result = await h.agent()
            assert len(plan["calls"]) == len(result["tools"]) == 1
            assert not any(body.get("tools") for body in h.fixture.calls["text"])
            assert any(body.get("tools") for body in h.fixture.calls["tools"])

    asyncio.run(run())


def test_INT_010_transient_same_target_fallback_executes_tool_once(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            h.fixture.models.clear()
            h.fixture.add("first", priority=10, responder=lambda _: httpx.Response(503))
            h.fixture.add("second", responder=h.respond)
            h.fixture.state(fallback_enabled=True)
            plan, result = await h.agent()
            assert len(result["tools"]) == 1 and len(h.fixture.calls["first"]) == 1
            assert h.fixture.calls["second"]
            detail = h.app.state.checkpoint_manager.detail(plan["plan_id"], h.owner)
            assert len(detail["checkpoints"]) == 1
            assert detail["checkpoints"][0]["step_state"] == "COMMITTED"
            assert (
                sum(row["decision"].get("is_fallback", False) for row in h.fixture.ledger.recent())
                == 1
            )

    asyncio.run(run())


def test_INT_011_scheduled_rag_shared_durable_router_budget_and_unknown_price(tmp_path):
    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            h.fixture.state(max_requests_per_task=1)
            key = await scheduled_run(h, report_graph(collection, second_model=True))
            # The captured run keeps its original durable ledger. INT014 separately
            # proves a fresh process reads reservations after a hard exit.
            final, run, _ = await complete_schedule(h, key)
            assert run["state"] == "FAILED" and run["error_code"] == "TASK_REQUEST_LIMIT", run
            assert len(h.requests) == 1 and final["status"] in {"FAILED", "NEEDS_REVIEW"}
            from sqlalchemy import select
            from app.models.model_routing import ModelRequestRow

            with SessionLocal() as db:
                rows = db.scalars(
                    select(ModelRequestRow).where(ModelRequestRow.task_id == run["run_id"])
                ).all()
            assert len(rows) == 1 and rows[0].workflow_id == run["run_id"]
            h.fixture.state(budget_mode="ESTIMATED", max_cost_per_request=1)
            with pytest.raises(RoutingError):
                await h.app.state.model_roles.capture().service("character_model").chat([])
            assert len(h.requests) == 1

    asyncio.run(run())


def test_INT_012_voice_final_never_grants_computer_window_permission(tmp_path):
    from app.services.realtime_voice.contracts import StartRequest, Scope, Frame
    from app.services.evaluation.voice_cases import signal

    async def run():
        async with unified_host(tmp_path) as h:
            manager = h.app.state.realtime_voice
            options = StartRequest(
                owner_id=uuid4(), conversation_id=h.owner, character_id="assistant"
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
            for n in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(n), timestamp=0))
            await manager.finalize(scope)
            manager.claim(scope, h.owner, "assistant")
            h.tool = (
                "computer_click",
                {
                    "target_id": str(uuid4()),
                    "observation_id": str(uuid4()),
                    "candidate_id": str(uuid4()),
                    "verification": {"kind": "window_focused"},
                },
            )
            calls = []

            async def forbidden(*args, **kwargs):
                calls.append(args)
                raise AssertionError("Voice transcript must not authorize a window")

            h.app.state.desktop_bridge = SimpleNamespace(request=forbidden)
            plan = await h.app.state.agent_service.plan(
                h.app,
                "assistant",
                h.owner,
                "computer use 观察窗口并点击",
                model_routes=manager.owned(scope, turn=True).turn.model_routes,
            )
            assert plan["calls"] and all(c["requires_confirmation"] for c in plan["calls"])
            result = await h.app.state.agent_service.execute(
                h.app, plan["plan_id"], set(), "natural", False, 1
            )
            assert not calls and all(t["status"] != "done" for t in result["tools"])

    asyncio.run(run())


def test_INT_013_character_memory_conversation_and_voice_snapshot_isolation(tmp_path):
    from app.services.character.loader import Character, CharacterProfile
    from app.services.realtime_voice.contracts import StartRequest, Scope

    async def run():
        async with unified_host(tmp_path) as h:
            loader = h.app.state.characters
            for suffix in ("alpha", "beta"):
                char = Character(
                    id="custom_" + suffix,
                    name=suffix,
                    profile=CharacterProfile(identity="IDENTITY_" + suffix),
                    system_prompt="PERSONA_" + suffix,
                )
                loader.characters[char.id] = char
                with SessionLocal.begin() as db:
                    owner = str(uuid4())
                    db.add(Conversation(id=owner, character_id=char.id, conversation_type="work"))
                    db.flush()
                    db.add(
                        ManualMemory(
                            character_id=char.id, owner_id=char.id, content="MANUAL_" + suffix
                        )
                    )
                    db.add(
                        Message(
                            character_id=char.id,
                            conversation_id=owner,
                            role="user",
                            content="叫我 MEMORY_" + suffix,
                        )
                    )
                session = h.app.state.work_state.ensure(owner)
                h.app.state.work_state.set_workspace(session, str(h.workspace))
                await h.app.state.agent_service.plan(
                    h.app, char.id, owner, "记得我的记忆，并读取所选项目中的 fixture.txt 后报告"
                )
                body = json.dumps(h.requests[-1], ensure_ascii=False)
                assert "IDENTITY_" + suffix in body and "MEMORY_" + suffix in body
                assert "MANUAL_" + suffix in body
                other = "beta" if suffix == "alpha" else "alpha"
                assert "IDENTITY_" + other not in body and "MEMORY_" + other not in body
                assert "MANUAL_" + other not in body
            manager = h.app.state.realtime_voice
            options = StartRequest(
                owner_id=uuid4(),
                conversation_id=h.owner,
                character_id="assistant",
                voice_id="synthetic-alpha-voice",
                speaking_rate=0.95,
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
            voice_config = manager.owned(scope, turn=True).turn.provider.config
            assert voice_config.voice_id == "synthetic-alpha-voice" and voice_config.speed == 0.95
            h.app.state.voice_client.config = SimpleNamespace(
                voice_id="synthetic-beta-voice", speed=1.05
            )
            old = manager.owned(scope, turn=True).turn.model_routes
            h.fixture.models.clear()
            h.fixture.add("new", api_model="synthetic-new", responder=h.respond)
            h.fixture.state()
            assert old.service("character_model").config.model == "synthetic"
            assert manager.owned(scope, turn=True).turn.provider.config is voice_config
            assert voice_config.voice_id == "synthetic-alpha-voice" and voice_config.speed == 0.95
            await manager.interrupt(scope)
            identity = await manager.begin(
                scope.model_copy(update={"turn_id": None, "generation_id": None})
            )
            new_scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
            assert (
                manager.owned(new_scope, turn=True)
                .turn.model_routes.service("character_model")
                .config.model
                == "synthetic-new"
            )

    asyncio.run(run())


def test_INT_015_same_host_four_conversations_cancel_isolation(tmp_path, monkeypatch):
    from sqlalchemy import select
    from app.models.model_routing import ModelRequestRow
    from app.api.chat_stream_v4 import router
    from app.services.realtime_voice.contracts import StartRequest, Scope, Frame
    from app.services.evaluation.voice_cases import signal

    async def run():
        async with unified_host(tmp_path) as h:
            h.app.include_router(router)
            h.fixture.state(max_requests_per_task=1)
            owners = [h.owner]
            for _ in range(3):
                with SessionLocal.begin() as db:
                    key = str(uuid4())
                    db.add(Conversation(id=key, character_id="assistant", conversation_type="work"))
                owners.append(key)
                h.app.state.work_state.ensure(key)
            barrier, release = asyncio.Event(), asyncio.Event()
            original = h.app.state.workflows.executor.execute

            async def execute(node, *args):
                if node.node_id == "pause_here":
                    barrier.set()
                    await release.wait()
                return await original(node, *args)

            monkeypatch.setattr(h.app.state.workflows.executor, "execute", execute)
            pending = chain(
                Node(
                    node_id="pause_here",
                    type="transform",
                    config={"operation": "concat", "value": ["waiting"]},
                ),
                Node(node_id="finish", type="output", config={"value": "done"}),
            )
            key = start(h.app, owners[1], pending)
            await asyncio.wait_for(barrier.wait(), 3)
            manager = h.app.state.realtime_voice
            options = StartRequest(
                owner_id=uuid4(), conversation_id=owners[2], character_id="assistant"
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
            for n in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(n), timestamp=0))
            final = await manager.finalize(scope)
            fourth = SimpleNamespace(**{**h.__dict__, "owner": owners[3]})
            schedule = await scheduled_run(
                fourth,
                chain(
                    Node(
                        node_id="model",
                        type="llm_agent",
                        config={
                            "instruction": "Generate a synthetic concurrent report",
                            "value": {},
                        },
                    ),
                    Node(
                        node_id="finish",
                        type="output",
                        config={"value": {"$ref": "nodes.model.output"}},
                    ),
                ),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=h.app), base_url="http://127.0.0.1"
            ) as client:
                chat_task = asyncio.create_task(
                    client.post(
                        "/api/chat/stream",
                        json={
                            "character_id": "assistant",
                            "conversation_id": owners[0],
                            "message": "CHAT_SCOPE_MARKER",
                            "voice_enabled": False,
                        },
                    )
                )
                voice_task = asyncio.create_task(
                    client.post(
                        "/api/chat/stream",
                        json={
                            "character_id": "assistant",
                            "conversation_id": owners[2],
                            "renderer_id": str(options.owner_id),
                            "message": final["text"],
                            "voice_enabled": True,
                            "voice_context": {
                                k: str(v) for k, v in scope.model_dump().items() if k != "owner_id"
                            },
                        },
                    )
                )
                await asyncio.sleep(0)
                await h.app.state.workflows.control(key, owners[1], "cancel")
                release.set()
                chat, voice, scheduled_result = await asyncio.gather(
                    chat_task, voice_task, complete_schedule(fourth, schedule)
                )
            assert chat.status_code == voice.status_code == 200
            assert (
                "event: done" in chat.text
                and "event: audio_pcm" in voice.text
                and "event: done" in voice.text
            )
            assert (await settle(h.app, owners[1], key))["state"] == "CANCELLED"
            assert scheduled_result[0]["status"] == "COMPLETED"
            with SessionLocal() as db:
                chat_rows = db.scalars(
                    select(Message).where(Message.conversation_id == owners[0])
                ).all()
                voice_rows = db.scalars(
                    select(Message).where(Message.conversation_id == owners[2])
                ).all()
                requests = db.scalars(
                    select(ModelRequestRow).where(
                        ModelRequestRow.workflow_id == scheduled_result[1]["run_id"]
                    )
                ).all()
            assert any(r.content == "CHAT_SCOPE_MARKER" for r in chat_rows)
            assert not any(r.content == "CHAT_SCOPE_MARKER" for r in voice_rows)
            assert len(requests) == 1 and requests[0].task_id == scheduled_result[1]["run_id"]
            assert len(h.requests) == 3 and not h.app.state.runtime_registry.active
            with pytest.raises(ValueError):
                h.app.state.workflows.repository.run(key, owners[3])

    asyncio.run(run())


def test_INT_016_authorized_scheduled_rag_writes_verified_report(tmp_path):
    from app.services.scheduling.notifications import SchedulerNotifications

    async def run():
        async with unified_host(tmp_path) as h:
            collection = await h.indexed()
            key = await scheduled_run(h, report_graph(collection))
            final, run, approvals = await complete_schedule(h, key)
            assert run["state"] == "COMPLETED", run
            target = h.workspace / "report.md"
            content = target.read_text(encoding="utf-8")
            assert "8794" in content and "[K:" in content
            import re
            from app.services.knowledge.contracts import SearchQuery

            sources = h.app.state.knowledge.retriever.search(
                SearchQuery(query="Orchard", collection_ids=[collection]), h.owner, "assistant"
            )["results"]
            cited = set(re.findall(r"\[K:([^\]]+)\]", content))
            assert cited and cited <= {row["chunk_id"] for row in sources}
            for source in sources:
                if source["chunk_id"] in cited:
                    resolved = h.app.state.knowledge.retriever.citations.resolve(
                        source, h.owner, "assistant"
                    )
                    assert resolved

            evidence = run["output"]["finish"]
            assert evidence["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
            assert evidence["size_bytes"] == target.stat().st_size > 0
            assert len(h.requests) == 1 and run["tool_calls"] == 2 and approvals
            assert final["status"] == "COMPLETED"
            assert SchedulerNotifications(h.scheduler.store).listing(h.owner)
            traces = h.app.state.trace_repository.listing(h.owner)["items"]
            events = [
                event
                for row in traces
                for event in h.app.state.trace_repository.events(row["trace_id"], h.owner)["items"]
            ]
            assert {row["event_type"] for row in events} >= {
                "MODEL_ROUTING_DECIDED",
                "WORKFLOW_COMPLETED",
                "TOOL_COMPLETED",
            }
            for node in run["nodes"]:
                if node.get("recovery_task_id"):
                    detail = h.app.state.checkpoint_manager.detail(
                        node["recovery_task_id"], h.owner
                    )
                    assert detail["checkpoints"][0]["step_state"] == "COMMITTED"
            # Independent deterministic artifact grading; no model success claim is trusted.
            grade = {
                "port": "8794" in content,
                "citation": "[K:" in content,
                "file_hash": evidence["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest(),
                "owned_trace": all(row["conversation_id"] == h.owner for row in traces),
            }
            assert all(grade.values())
            from app.services.evaluation.integration_observation import evaluate_workflow_artifact
            from app.services.evaluation.repository import EvaluationRepository
            from app.services.evaluation.reports import EvaluationReportService

            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from app.database.migrations import migrate

            evaluation_engine = create_engine(
                f"sqlite:///{(tmp_path / 'integration-evaluation.db').as_posix()}"
            )
            migrate(evaluation_engine)
            repository = EvaluationRepository(sessionmaker(evaluation_engine))
            evaluated = evaluate_workflow_artifact(h.app, run["run_id"], h.owner, repository)
            assert evaluated["passed_cases"] == 1
            exported = EvaluationReportService(repository).export(evaluated["run_id"], "json")[
                "content"
            ]
            assert "integration_observation" in exported and evidence["sha256"] in exported
            assert run["run_id"] in exported and "no worker rerun" in exported
            for format in ("csv", "markdown"):
                rendered = EvaluationReportService(repository).export(evaluated["run_id"], format)[
                    "content"
                ]
                assert "INT-016" in rendered and "Integration Observation" in rendered
            target.write_text(content + "\nchanged after run", encoding="utf-8")
            changed = evaluate_workflow_artifact(h.app, run["run_id"], h.owner, repository)
            assert changed["failed_cases"] == 1 and len(h.requests) == 1
            evaluation_engine.dispose()

    asyncio.run(run())


def test_INT_014_actual_process_exit_recovers_scheduler_workflow_trace_and_budget(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    root = tmp_path / "hard-exit"
    root.mkdir()
    project = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "COMPANION_DATA_DIR": str(root / "profile"),
        "PYTHONPATH": os.pathsep.join([str(project / "backend"), str(project / "backend/tests")]),
    }
    script = project / "backend/tests/unified_crash_probe.py"
    first = subprocess.run(
        [sys.executable, "-B", str(script), "crash", str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert first.returncode == 73, first.stdout + first.stderr
    second = subprocess.run(
        [sys.executable, "-B", str(script), "reopen", str(root)],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    report = json.loads((root / "result.json").read_text(encoding="utf-8"))
    assert report["side_effect_unchanged"] and report["budget_survived_process"]
    assert report["automatic_replay_count"] == 0 and report["trace_rows"] > 0


def test_chat_budget_denial_retains_safe_http_and_stream_reason(tmp_path):
    from app.api.chat import router as chat_router
    from app.api.chat_stream_v4 import router as stream_router

    async def run():
        async with unified_host(tmp_path) as h:
            h.app.include_router(chat_router)
            h.app.include_router(stream_router)
            h.fixture.state(max_requests_per_day=1)
            payload = {
                "character_id": "assistant",
                "conversation_id": h.owner,
                "message": "本地预算验证",
                "voice_enabled": False,
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=h.app), base_url="http://isolated.test"
            ) as client:
                first = await client.post("/api/chat", json=payload)
                assert first.status_code == 200, first.text
                denied = await client.post("/api/chat", json=payload)
                assert denied.status_code == 409, denied.text
                assert denied.json()["detail"]["code"] == "DAY_REQUEST_LIMIT"
                assert "上限" in denied.json()["detail"]["message"]
                stream = await client.post("/api/chat/stream", json=payload)
                assert (
                    '"code": "DAY_REQUEST_LIMIT"' in stream.text
                    or '"code":"DAY_REQUEST_LIMIT"' in stream.text
                )
                assert "回复生成失败" not in stream.text
                assert len(h.requests) == 1

    asyncio.run(run())

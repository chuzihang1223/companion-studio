"""Explicit, real-duration load; excluded from the ordinary test filename glob.

Run this file explicitly with pytest. The default is one 60-minute run including
a 30-minute core phase; a shorter diagnostic run never satisfies that gate.
Production services run in one owned host. Models/ASR/PCM and scheduler calendar
are synthetic, and cannot establish hardware, voice quality or live-provider SLAs.
"""

import asyncio
import ctypes
import gc
import hashlib
import json
import os
from pathlib import Path
import threading
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import win32api
import win32process
from sqlalchemy import event

from unified_skill_fixture import unified_host
from unified_skill_scenarios import complete_schedule, scheduled_run
from workflow_fixture import graph, settle, start
from app.database.session import SessionLocal, engine
from app.models import Conversation
from app.services.evaluation.voice_cases import signal
from app.services.knowledge.contracts import SearchQuery
from app.services.realtime_voice.contracts import Frame, Scope, StartRequest


def process_metrics():
    handle = win32api.GetCurrentProcess()
    memory = win32process.GetProcessMemoryInfo(handle)
    handles = ctypes.c_ulong()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if not kernel.GetProcessHandleCount(ctypes.c_void_p(int(handle)), ctypes.byref(handles)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {
        "rss_bytes": memory["WorkingSetSize"],
        "private_bytes": memory["PagefileUsage"],
        "peak_rss_bytes": memory["PeakWorkingSetSize"],
        "handle_count": handles.value,
        "python_threads": threading.active_count(),
        "cpu_seconds": time.process_time(),
    }


async def voice_cycle(h, owner):
    manager = h.app.state.realtime_voice
    options = StartRequest(owner_id=uuid4(), conversation_id=owner, character_id="assistant")
    identity = await manager.start(options)
    identity = await manager.begin(
        Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
    )
    scope = Scope.model_validate({key: identity[key] for key in Scope.model_fields})
    try:
        for sequence in range(3):
            manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
        final = await manager.finalize(scope)
        assert final["kind"] == "FINAL" and final["text"]
        await manager.interrupt(scope)
        try:
            manager.frame(Frame(**scope.model_dump(), **signal(3), timestamp=0))
        except LookupError:
            pass
        else:
            raise AssertionError("Cancelled voice generation accepted a late frame")
        return {"voice_session_id": str(identity["voice_session_id"]), "voice_final": final["kind"]}
    finally:
        await manager.stop_owner(options.owner_id)


def test_real_duration_original_services(tmp_path):
    duration = float(os.environ.get("HARDENING_SOAK_SECONDS", "3600"))
    assert duration >= 30
    output = Path(os.environ["HARDENING_SOAK_REPORT"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "RUNNING",
        "requested_seconds": duration,
        "started_utc": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "boundaries": {
            "host": "original single-host production services with real SQLite and stdio MCP",
            "models": "httpx.MockTransport; no paid or external model requests",
            "speech": "synthetic PCM/ASR; no physical microphone or speaker assertion",
            "scheduler_calendar": "controlled UTC clock; soak duration uses real monotonic time",
            "process_scope": "this Python process and its recorded owned MCP children",
        },
        "samples": [],
        "cycles": 0,
        "workload_receipts": [],
        "schedule_receipts": [],
        "cancel_receipts": [],
        "mcp_children": [],
        "sqlite_errors": [],
        "unexpected_async_errors": [],
    }
    source = Path(__file__).resolve().parents[2]
    bound = [
        *sorted((source / "backend/app").rglob("*.py")),
        Path(__file__),
        source / "backend/tests/unified_skill_fixture.py",
        source / "backend/tests/unified_skill_scenarios.py",
        source / "backend/tests/workflow_fixture.py",
        source / "backend/tests/scheduler_fixture.py",
    ]
    report["source_files"] = {
        file.relative_to(source).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in bound
    }
    report["source_revision"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()

    def persist():
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output)

    def database_error(context):
        error = context.original_exception
        report["sqlite_errors"].append(
            {
                "type": type(error).__name__,
                "sqlite_errorname": getattr(error, "sqlite_errorname", None),
            }
        )

    async def run():
        began = time.monotonic()
        event.listen(engine, "handle_error", database_error)
        loop = asyncio.get_running_loop()
        original_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda _loop, context: report["unexpected_async_errors"].append(
                {
                    "type": type(context.get("exception")).__name__,
                    "message": "async exception observed",
                }
            )
        )
        children = []
        try:
            async with unified_host(tmp_path) as h:
                owners = []
                with SessionLocal.begin() as db:
                    for _ in range(4):
                        key = str(uuid4())
                        db.add(
                            Conversation(id=key, character_id="assistant", conversation_type="work")
                        )
                        owners.append(key)
                collection = await h.indexed()
                query = SearchQuery(query="Orchard", collection_ids=[collection])
                schedule_host = SimpleNamespace(**{**h.__dict__, "owner": owners[2]})
                while time.monotonic() - began < duration:
                    cycle_started = time.monotonic()
                    index = report["cycles"]
                    definition = graph()
                    key = start(h.app, owners[0], definition)
                    agent, workflow, voice = await asyncio.gather(
                        h.agent(), settle(h.app, owners[0], key), voice_cycle(h, owners[1])
                    )
                    assert agent[1]["tools"] and workflow["state"] == "COMPLETED"
                    rows = h.app.state.knowledge.retriever.search(query, h.owner, "assistant")
                    assert rows["results"] and rows["mode"] == "keyword"
                    if index % 6 == 0:
                        tool = await h.connect_mcp()
                        manager = h.app.state.mcp_connections
                        child = manager.connections[tool["server_id"]].transport.process
                        children.append(child)
                        reply = await manager.invoke(
                            tool["server_id"], "call_tool", "echo", {"text": "owned soak evidence"}
                        )
                        assert reply.content[0].text == "owned soak evidence"
                        await manager.remove(tool["server_id"])
                        assert child.returncode is not None
                        report["mcp_children"].append(
                            {"pid": child.pid, "exit_code": child.returncode}
                        )
                    # The production per-owner scheduler quota is 12/hour.
                    # Exercise it every five minutes; never reset its ledger or
                    # advance the synthetic clock to manufacture extra budget.
                    if index % 30 == 0:
                        occurrence = await scheduled_run(schedule_host, graph())
                        final, scheduled_result, _ = await complete_schedule(
                            schedule_host, occurrence
                        )
                        assert (
                            final["status"] == "COMPLETED"
                            and scheduled_result["state"] == "COMPLETED"
                        )
                        report["schedule_receipts"].append(
                            {
                                "cycle": index,
                                "occurrence_id": occurrence,
                                "workflow_run_id": scheduled_result["run_id"],
                                "status": final["status"],
                            }
                        )
                    if time.monotonic() - began >= 1800 and index % 3 == 0:
                        # Cancellation is exercised through the original permission wait.
                        pending = start(
                            h.app,
                            owners[3],
                            graph("human_approval", {"message": "soak gate", "value": True}),
                        )
                        waiting = await settle(h.app, owners[3], pending, waiting=True)
                        assert waiting["state"] == "WAITING_PERMISSION"
                        await h.app.state.workflows.control(pending, owners[3], "cancel")
                        assert (await settle(h.app, owners[3], pending))["state"] == "CANCELLED"
                        report["cancel_receipts"].append(
                            {"cycle": index, "workflow_run_id": pending, "state": "CANCELLED"}
                        )
                    await h.trace.flush()
                    await asyncio.sleep(0.05)
                    assert not h.app.state.runtime_registry.active
                    assert not h.app.state.realtime_voice.rows
                    assert not h.app.state.mcp_connections.connections
                    assert not h.app.state.mcp_connections.locks
                    assert not h.app.state.mcp_connections.lock_users
                    assert not h.app.state.external_tools.tools
                    assert not h.app.state.workflows.tasks
                    report["cycles"] += 1
                    # The transport fixture's captured request bodies are test-only
                    # observations. Keep counts, not unbounded duplicate payloads.
                    report["synthetic_model_requests"] = report.get(
                        "synthetic_model_requests", 0
                    ) + len(h.requests)
                    h.requests.clear()
                    for calls in h.fixture.calls.values():
                        calls.clear()
                    gc.collect()
                    sample = {
                        "cycle": index,
                        "elapsed_seconds": time.monotonic() - began,
                        "phase": "core" if time.monotonic() - began < 1800 else "mixed_cancel",
                        **process_metrics(),
                        "trace_queue": h.trace.queue.qsize(),
                        "trace_metrics": dict(h.trace.metrics),
                        "owned_live_children": sum(child.returncode is None for child in children),
                        "workflow_tasks": len(h.app.state.workflows.tasks),
                        "mcp_connections": len(h.app.state.mcp_connections.connections),
                        "cycle_seconds": time.monotonic() - cycle_started,
                    }
                    report["workload_receipts"].append(
                        {
                            "cycle": index,
                            "phase": sample["phase"],
                            "agent_plan_id": agent[0]["plan_id"],
                            "workflow_run_id": workflow["run_id"],
                            "workflow_state": workflow["state"],
                            **voice,
                            "rag_results": len(rows["results"]),
                            "rag_content_sha256": hashlib.sha256(
                                json.dumps(rows, sort_keys=True).encode()
                            ).hexdigest(),
                        }
                    )
                    report["samples"].append(sample)
                    persist()
                    await asyncio.sleep(
                        max(
                            0,
                            min(
                                10 - sample["cycle_seconds"], duration - (time.monotonic() - began)
                            ),
                        )
                    )
            assert all(child.returncode is not None for child in children)
            assert not report["sqlite_errors"] and not report["unexpected_async_errors"]
            report["status"] = "PASS" if duration >= 3600 else "DIAGNOSTIC_PASS_ONLY"
        except BaseException as error:
            report["status"] = "FAIL"
            report["failure_type"] = type(error).__name__
            raise
        finally:
            report["actual_seconds"] = time.monotonic() - began
            report["real_core_phase_seconds"] = min(1800, report["actual_seconds"])
            report["owned_live_children_at_close"] = sum(
                child.returncode is None for child in children
            )
            event.remove(engine, "handle_error", database_error)
            loop.set_exception_handler(original_handler)
            persist()

    asyncio.run(run())

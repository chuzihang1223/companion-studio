"""Explicit, real-duration load; excluded from the ordinary test filename glob.

Run this file explicitly with pytest. The default is one 60-minute run including
a 30-minute core phase; a shorter diagnostic run never satisfies that gate.
Production services run in one owned host. Models/ASR/PCM and scheduler calendar
are synthetic, and cannot establish hardware, voice quality or live-provider SLAs.
"""

import asyncio
import ctypes
from ctypes import wintypes
import gc
import hashlib
import json
import os
from pathlib import Path
import threading
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import win32api
import win32process
from sqlalchemy import event, func, select
from trace_sql_observer import Recorder

from unified_skill_fixture import unified_host
from unified_skill_scenarios import complete_schedule, scheduled_run
from workflow_fixture import graph, settle, start
from app.database.session import SessionLocal, engine
from app.models import Conversation
from app.models.agent_trace import TraceEvent, TraceProjectionTotals
from app.services.evaluation.voice_cases import signal
from app.services.knowledge.contracts import SearchQuery
from app.services.realtime_voice.contracts import Frame, Scope, StartRequest


class ThreadEntry(ctypes.Structure):
    # https://learn.microsoft.com/windows/win32/api/tlhelp32/ns-tlhelp32-threadentry32
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread", wintypes.DWORD),
        ("owner", wintypes.DWORD),
        ("priority", wintypes.LONG),
        ("delta", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


_threads = ctypes.WinDLL("kernel32", use_last_error=True)
_threads.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_threads.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
for _name in ("Thread32First", "Thread32Next"):
    getattr(_threads, _name).argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    getattr(_threads, _name).restype = wintypes.BOOL
_threads.CloseHandle.argtypes = [wintypes.HANDLE]
_threads.CloseHandle.restype = wintypes.BOOL


def system_thread_count():
    """Store only this process's count; close every read-only snapshot handle."""
    snapshot = _threads.CreateToolhelp32Snapshot(0x00000004, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        count = 0
        found = _threads.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            count += entry.owner == os.getpid()
            found = _threads.Thread32Next(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise ctypes.WinError(ctypes.get_last_error())
        assert count >= 1
        return count
    finally:
        if not _threads.CloseHandle(snapshot):
            raise ctypes.WinError(ctypes.get_last_error())


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
        "system_threads": system_thread_count(),
        "python_allocated_blocks": sys.getallocatedblocks(),
        "cpu_seconds": time.process_time(),
    }


def live_resources(h):
    state = h.app.state
    return {
        "agent_pending": len(state.agent_service.pending),
        "agent_handles": len(state.task_runtime.handles),
        "agent_handle_references": sum(state.task_runtime._handle_users.values()),
        "runtime_active": len(state.runtime_registry.active),
        "runtime_session_owners": len(state.runtime_registry.by_session),
        "runtime_executions": len(state.task_runtime.executions),
        "stream_keys": len(state.work_state.subscribers),
        "stream_queues": sum(len(bucket) for bucket in state.work_state.subscribers.values()),
        "workflow_tasks": len(state.workflows.tasks),
        "voice_sessions": len(state.realtime_voice.rows),
        "mcp_connections": len(state.mcp_connections.connections),
        "business_checked_out": engine.pool.checkedout(),
        "trace_checked_out": h.trace.engine.pool.checkedout(),
    }


def retained_size(value, seen=None):
    """Size only our explicit diagnostic containers, not a process heap dump."""
    from collections import deque

    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(retained_size(k, seen) + retained_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list, deque)):
        size += sum(retained_size(item, seen) for item in value)
    return size


def pool_retention(h):
    return {
        label: {
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "configured_size": pool.size(),
            "overflow": pool.overflow(),
        }
        for label, pool in (("business", engine.pool), ("trace", h.trace.engine.pool))
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
        source / "backend/tests/trace_sql_observer.py",
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
        observer = None
        try:
            async with unified_host(tmp_path) as h:
                observer = Recorder({"business": engine, "trace": h.trace.engine})
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
                minimum = 360 if duration >= 3600 else 0
                while (
                    time.monotonic() - began < duration
                    or report["cycles"] < minimum
                    or minimum
                    and len(report["cancel_receipts"]) < 60
                ):
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
                        # Keep every exit receipt, but not a strong reference to
                        # every completed Process. On Windows those references
                        # each retain a handle and distort the resource trend.
                        # Any unconfirmed child stays owned in children/finally.
                        children.remove(child)
                        del child
                    # The production per-owner scheduler quota is 12/hour.
                    # Exercise it every five minutes; never reset its ledger or
                    # advance the synthetic clock to manufacture extra budget.
                    if index % 30 == 0 and len(report["schedule_receipts"]) < 12:
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
                    assert await h.trace.flush(), "Accepted Trace sources did not commit"
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
                    elapsed = time.monotonic() - began
                    sample = {
                        "cycle": index,
                        "elapsed_seconds": elapsed,
                        "phase": "core" if elapsed < 1800 else "mixed_cancel",
                        **process_metrics(),
                        "trace_queue": h.trace.queue.qsize(),
                        "trace_metrics": dict(h.trace.metrics),
                        "business_connections_checked_out": engine.pool.checkedout(),
                        "trace_connections_checked_out": h.trace.engine.pool.checkedout(),
                        "trace_pending_sources": h.trace.journal.snapshot()["pending"],
                        "voice_sessions": len(h.app.state.realtime_voice.rows),
                        "trace_write_gate": h.trace.write_gate.snapshot(),
                        "owned_live_children": sum(child.returncode is None for child in children),
                        "workflow_tasks": len(h.app.state.workflows.tasks),
                        "mcp_connections": len(h.app.state.mcp_connections.connections),
                        "cycle_seconds": time.monotonic() - cycle_started,
                        "live_resources": live_resources(h),
                        "pool_retention": pool_retention(h),
                    }
                    assert not any(sample["live_resources"].values())
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
                # Preserve the original hour/cycles, then measure a separate
                # real idle minute; no fake clock replaces either duration.
                report["mixed_load_duration_seconds"] = time.monotonic() - began
                report["idle_samples"] = []
                idle_started = time.monotonic()
                for _ in range(13):
                    gc.collect()
                    idle = {
                        "idle_seconds": time.monotonic() - idle_started,
                        **process_metrics(),
                        "live_resources": live_resources(h),
                    }
                    assert not any(idle["live_resources"].values())
                    report["idle_samples"].append(idle)
                    persist()
                    if len(report["idle_samples"]) < 13:
                        await asyncio.sleep(5)
                report["diagnostic_retention"] = {
                    "observer_bytes": retained_size(
                        {
                            name: getattr(observer, name)
                            for name in (
                                "transactions",
                                "write_intervals",
                                "statement_waits",
                                "tail",
                                "busy_intervals",
                                "connections",
                                "counters",
                                "engines",
                            )
                        }
                    ),
                    "owned_report_bytes": retained_size(report),
                    "observer_sample_cap_per_series": observer.sample_limit,
                    "classification": "EXPECTED_RETENTION",
                    "limit": "Owned diagnostic container sizes are not a numeric reconciliation of RSS/private bytes; residual growth remains UNATTRIBUTED_GROWTH unless separately proven.",
                }
                journal = h.trace.journal.snapshot()
                with SessionLocal() as db:
                    totals = db.get(TraceProjectionTotals, 1)
                    retained = db.scalar(select(func.count()).select_from(TraceEvent))
                    accounting = {
                        field: getattr(totals, field)
                        for field in (
                            "committed_commands",
                            "committed_events",
                            "legacy_events",
                            "recovery_events",
                            "pruned_events",
                            "pruned_traces",
                            "limited_events",
                        )
                    }
                with engine.connect() as connection:
                    report["database_integrity"] = connection.exec_driver_sql(
                        "PRAGMA integrity_check"
                    ).scalar()
                with h.trace.journal.transaction() as connection:
                    report["journal_integrity"] = connection.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0]
                report["trace_accounting"] = {
                    "generated_commands": h.trace.metrics["generated"],
                    "generated_events": h.trace.metrics["generated_events"],
                    "journal": journal,
                    "projection": accounting,
                    "retained_events": retained,
                }
                assert (
                    journal["accepted"] == journal["committed"] == accounting["committed_commands"]
                )
                assert (
                    journal["accepted_events"]
                    == journal["committed_events"]
                    == accounting["committed_events"]
                )
                assert retained == (
                    accounting["legacy_events"]
                    + accounting["committed_events"]
                    + accounting["recovery_events"]
                    - accounting["pruned_events"]
                )
                assert not journal["pending"] and not journal["write_failures"]
                assert not h.trace.metrics["write_failures"] and not h.trace.metrics["dropped"]
                report.update(
                    trace_events_generated=h.trace.metrics["generated_events"],
                    trace_events_accepted=journal["accepted_events"],
                    trace_events_committed=journal["committed_events"],
                    trace_events_recoverably_pending=h.trace.metrics["recoverably_pending"],
                    trace_write_failures=h.trace.metrics["write_failures"],
                    trace_dropped_commands=h.trace.metrics["dropped"],
                    sqlite_busy_transient=h.trace.metrics["sqlite_busy_transient"],
                    sqlite_busy_terminal=h.trace.metrics["sqlite_busy_terminal"],
                    retry_count=h.trace.metrics["write_retries"],
                    queue_peak=h.trace.metrics["queue_peak"],
                    queue_final=h.trace.queue.qsize(),
                )
            # Observe shutdown too: the owned Trace Engine is disposed by host.
            gc.collect()
            report["resource_cleanup"] = {
                **process_metrics(),
                "business_checked_out": engine.pool.checkedout(),
                "trace_checked_out": h.trace.engine.pool.checkedout(),
                "trace_journal_connection_closed": h.trace.journal.connection is None,
                "trace_writer_done": h.trace.worker.done(),
                "trace_queue": h.trace.queue.qsize(),
                "workflow_tasks": len(h.app.state.workflows.tasks),
                "voice_sessions": len(h.app.state.realtime_voice.rows),
                "live_resources": live_resources(h),
                "pool_retention": pool_retention(h),
            }
            assert not any(report["resource_cleanup"]["live_resources"].values())
            assert report["resource_cleanup"]["business_checked_out"] == 0
            assert report["resource_cleanup"]["trace_checked_out"] == 0
            assert report["resource_cleanup"]["trace_journal_connection_closed"]
            if duration >= 3600:
                assert report["cycles"] >= 360 and len(report["mcp_children"]) >= 60
                assert (
                    len(report["schedule_receipts"]) == 12 and len(report["cancel_receipts"]) >= 60
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
            report["actual_duration_seconds"] = report["actual_seconds"]
            report["completed_cycles"] = report["cycles"]
            if observer:
                report["sql_observation"] = observer.snapshot()
                report["sqlite_transaction_p95"] = report["sql_observation"]["transaction"][
                    "p95_ms"
                ]
                observer.close()
            report["real_core_phase_seconds"] = min(1800, report["actual_seconds"])
            report["owned_live_children_at_close"] = sum(
                child.returncode is None for child in children
            )
            event.remove(engine, "handle_error", database_error)
            loop.set_exception_handler(original_handler)
            persist()

    asyncio.run(run())

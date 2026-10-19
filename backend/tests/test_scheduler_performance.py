"""Measured local fixture performance; no claim of real AI quality or end-user SLA."""

import asyncio
import ctypes
from datetime import timedelta
import json
import os
import time
from uuid import uuid4

from app.models.schedules import ScheduledOccurrenceRow
from app.services.scheduling.coordinator import DurableScheduler
from app.services.scheduling.time_rules import stamp
from scheduler_fixture import scheduler_host, scheduled, run_to_completion


def memory_bytes():
    if os.name != "nt":
        return None
    from ctypes import wintypes as w

    class Counters(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("faults", w.DWORD)] + [
            (key, ctypes.c_size_t)
            for key in (
                "peak_working",
                "working",
                "peak_pool",
                "pool",
                "peak_nonpaged",
                "nonpaged",
                "pagefile",
                "peak_pagefile",
            )
        ]

    counters = Counters()
    counters.size = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    api = ctypes.WinDLL("psapi", use_last_error=True)
    api.GetProcessMemoryInfo.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD]
    if not api.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.size
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.working


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int((len(values) - 1) * fraction))]


def test_measured_hundred_schedules_thousand_occurrences_and_idle_loop(tmp_path, monkeypatch):
    async def run():
        opened = time.perf_counter()
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            startup_ms = (time.perf_counter() - opened) * 1000
            definition = scheduled(app, owner, clock)
            start = time.perf_counter()
            for n in range(100):
                scheduler.save(
                    definition.model_copy(
                        update={"schedule_id": str(uuid4()), "name": "Load fixture " + str(n)}
                    )
                )
            create_ms = (time.perf_counter() - start) * 1000
            measurements = []
            for _ in range(20):
                start = time.perf_counter()
                definitions = scheduler.store.listing(owner)
                measurements.append((time.perf_counter() - start) * 1000)
            assert len(definitions) == 100
            key = definitions[0]["definition"]["schedule_id"]
            dispatch, complete, queue = [], [], []
            original_claim = scheduler.store.claim
            queued_at = None

            def observed_claim(*args, **kwargs):
                result = original_claim(*args, **kwargs)
                if result and queued_at is not None:
                    queue.append((time.perf_counter() - queued_at) * 1000)
                return result

            monkeypatch.setattr(scheduler.store, "claim", observed_claim)
            for _ in range(10):
                start = time.perf_counter()
                row = scheduler.run_now(key, owner)
                queued_at = time.perf_counter()
                await scheduler.tick()
                dispatch.append((time.perf_counter() - start) * 1000)
                await run_to_completion(app, scheduler, owner, row["occurrence_id"])
                complete.append((time.perf_counter() - start) * 1000)
            monkeypatch.setattr(scheduler.store, "claim", original_claim)
            entered, release = asyncio.Event(), asyncio.Event()
            original = app.state.workflows.executor.execute

            async def barrier(node, *args):
                if node.type == "transform":
                    entered.set()
                    await release.wait()
                return await original(node, *args)

            monkeypatch.setattr(app.state.workflows.executor, "execute", barrier)
            active = scheduler.run_now(key, owner)
            await scheduler.tick()
            await asyncio.wait_for(entered.wait(), 5)
            body = json.dumps(scheduler.store.occurrence(active["occurrence_id"], owner)["body"])
            start = time.perf_counter()
            with scheduler.store.transaction() as db:
                for n in range(1000):
                    db.add(
                        ScheduledOccurrenceRow(
                            id=str(uuid4()),
                            schedule_id=key,
                            owner_id=owner,
                            revision=1,
                            scheduled_for=stamp(clock()),
                            fingerprint=uuid4().hex,
                            status="NEEDS_REVIEW",
                            body=body,
                            created_at=stamp(clock() + timedelta(seconds=n + 1)),
                            attempt=0,
                        )
                    )
            seed_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            assert len(scheduler.store.occurrences(owner, limit=1000)) == 1000
            history_ms = (time.perf_counter() - start) * 1000
            release.set()
            final = await run_to_completion(app, scheduler, owner, active["occurrence_id"])
            assert final["status"] == "COMPLETED", (
                "1000 newer reviews must not hide older active run"
            )
            # Measure only scheduler loop overhead, with no model or GUI work.
            before = memory_bytes()
            wall, cpu = time.perf_counter(), time.process_time()
            scheduler.start()
            await asyncio.sleep(10)
            cpu_ms = (time.process_time() - cpu) * 1000
            wall_ms = (time.perf_counter() - wall) * 1000
            after = memory_bytes()
            closing = time.perf_counter()
            await scheduler.close()
            close_ms = (time.perf_counter() - closing) * 1000
            result = {
                "schema_version": 1,
                "boundary": "single_process_original_runtime_fake_clock_pure_workflow",
                "schedule_count": 100,
                "synthetic_history_count": 1000,
                "startup_fixture_ms": startup_ms,
                "create_100_ms": create_ms,
                "load_100_p50_ms": percentile(measurements, 0.5),
                "load_100_p95_ms": percentile(measurements, 0.95),
                "insert_1000_ms": seed_ms,
                "history_1000_ms": history_ms,
                "active_not_masked": final["status"] == "COMPLETED",
                "manual_dispatch_samples": len(dispatch),
                "queue_samples": len(queue),
                "queue_empty_p50_ms": percentile(queue, 0.5),
                "queue_empty_p95_ms": percentile(queue, 0.95),
                "manual_dispatch_p50_ms": percentile(dispatch, 0.5),
                "manual_dispatch_p95_ms": percentile(dispatch, 0.95),
                "pure_completion_p50_ms": percentile(complete, 0.5),
                "pure_completion_p95_ms": percentile(complete, 0.95),
                "idle_window_ms": wall_ms,
                "idle_process_cpu_ms": cpu_ms,
                "idle_one_core_percent": cpu_ms / wall_ms * 100,
                "working_set_before_bytes": before,
                "working_set_after_bytes": after,
                "shutdown_ms": close_ms,
                "resources_released": not app.state.workflows.tasks
                and not app.state.runtime_registry.active
                and not scheduler.running,
                "real_external_requests": 0,
                "real_wall_clock_trigger_latency": "separate_scheduler_gui_evidence",
                "queue_latency_under_saturation": "not_measured_by_this_fixture",
            }
            (tmp_path / "scheduler-performance.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            assert result["resources_released"]
            # No machine-dependent speed thresholds masquerade as correctness gates.

    asyncio.run(run())

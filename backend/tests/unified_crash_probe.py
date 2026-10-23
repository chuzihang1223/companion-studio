"""Owned child process only. Phase crash exits after a real write, before checkpoint commit."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from unified_skill_fixture import unified_host
from unified_skill_scenarios import chain, scheduled_run, complete_schedule
from app.services.coding.workspace import CodingWorkspaceExecutor
from app.services.workflows.contracts import Node
from app.services.llm.routing.contracts import RoutingError


async def run(stage, root):
    phase = root / stage
    phase.mkdir()
    async with unified_host(phase) as h:
        h.fixture.state(max_requests_per_day=1)
        if stage == "crash":
            await h.app.state.model_roles.capture().service("character_model").chat([])
            original = CodingWorkspaceExecutor.execute
            loop = asyncio.get_running_loop()

            def crash_after_write(executor, call):
                with (root / "write-dispatches.jsonl").open("a", encoding="utf-8") as counter:
                    counter.write(json.dumps({"stage": "crash", "name": call["name"]}) + "\n")
                result = original(executor, call)
                if call["name"] == "workspace.create":
                    path = h.workspace / "durable-report.md"
                    info = h.scheduler.store.occurrences(h.owner)[0]
                    info = {
                        key: info[key] for key in ("occurrence_id", "workflow_run_id", "owner_id")
                    }
                    info.update(
                        path=str(path),
                        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        mtime_ns=path.stat().st_mtime_ns,
                    )
                    (root / "crash.json").write_text(json.dumps(info), encoding="utf-8")
                    asyncio.run_coroutine_threadsafe(h.trace.flush(), loop).result(5)
                    os._exit(73)
                return result

            CodingWorkspaceExecutor.execute = crash_after_write
            graph = chain(
                Node(
                    node_id="write",
                    type="tool",
                    config={
                        "name": "workspace.create",
                        "coding_mode": "write_workspace",
                        "arguments": {
                            "path": "durable-report.md",
                            "content": "Synthetic durable side effect",
                        },
                    },
                ),
                Node(node_id="finish", type="output", config={"value": "done"}),
            )
            key = await scheduled_run(h, graph)
            await complete_schedule(h, key)
            raise AssertionError("Crash hook was not executed")
        info = json.loads((root / "crash.json").read_text(encoding="utf-8"))
        h.app.state.checkpoint_manager.recover()
        h.app.state.work_state.recover_interrupted()
        row = h.app.state.workflows.repository.run(info["workflow_run_id"], info["owner_id"])
        original = CodingWorkspaceExecutor.execute
        phase = ["automatic"]

        def observe_dispatch(executor, call):
            with (root / "write-dispatches.jsonl").open("a", encoding="utf-8") as counter:
                counter.write(json.dumps({"stage": phase[0], "name": call["name"]}) + "\n")
            return original(executor, call)

        CodingWorkspaceExecutor.execute = observe_dispatch
        await h.scheduler.tick()
        preview = await h.app.state.workflows.recovery.preview(
            info["workflow_run_id"], info["owner_id"]
        )
        path = Path(info["path"])
        unchanged = (
            path.stat().st_mtime_ns == info["mtime_ns"]
            and hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
        )
        budget_blocked = False
        try:
            await h.app.state.model_roles.capture().service("character_model").chat([])
        except RoutingError as error:
            budget_blocked = error.code == "DAY_REQUEST_LIMIT"
        # Reconciliation may prove an exact create completed, or require review.
        # In either case no automatic replay is allowed after process loss.
        assert unchanged and not h.requests and budget_blocked
        assert row["state"] in {"INTERRUPTED", "NEEDS_REVIEW"}
        assert not h.app.state.workflows.tasks
        traces = h.app.state.trace_repository.listing(info["owner_id"])["items"]
        assert traces
        phase[0] = "explicit"
        scheduler_preview = await h.scheduler.recovery_preview(
            info["occurrence_id"], info["owner_id"]
        )
        if scheduler_preview["can_resume"]:
            await h.scheduler.resume(
                info["occurrence_id"], info["owner_id"], scheduler_preview["revision"]
            )
            from types import SimpleNamespace

            restored = SimpleNamespace(**{**h.__dict__, "owner": info["owner_id"]})
            final, finished, _ = await complete_schedule(restored, info["occurrence_id"])
            assert finished["state"] == "COMPLETED" and final["status"] == "COMPLETED"
        else:
            from app.services.scheduling.contracts import ScheduleError

            try:
                await h.scheduler.resume(
                    info["occurrence_id"], info["owner_id"], scheduler_preview["revision"]
                )
            except ScheduleError as error:
                assert error.code == "SCHEDULE_RESUME_BLOCKED"
            else:
                raise AssertionError("Uncertain recovery unexpectedly resumed")
        dispatches = [
            json.loads(line)
            for line in (root / "write-dispatches.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        replay_count = sum(item["stage"] == "automatic" for item in dispatches)
        assert (
            replay_count == 0
            and sum(item["name"] == "workspace.create" for item in dispatches) == 1
        )
        assert path.stat().st_mtime_ns == info["mtime_ns"]
        report = {
            "hard_exit_code": 73,
            "side_effect_unchanged": unchanged,
            "automatic_replay_count": replay_count,
            "observed_dispatches": len(dispatches),
            "explicit_recovery_checked": True,
            "budget_survived_process": budget_blocked,
            "workflow_state": row["state"],
            "can_resume_after_reconciliation": preview["can_resume"],
            "trace_rows": len(traces),
            "occurrence_status": h.scheduler.store.occurrence(
                info["occurrence_id"], info["owner_id"]
            )["status"],
        }
        (root / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], Path(sys.argv[2])))

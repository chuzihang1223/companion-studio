"""Explicit repeated production-service startup/shutdown, never a soak substitute."""

import asyncio
from datetime import datetime, timezone
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from hardening_soak_workload import live_resources, pool_retention, process_metrics, voice_cycle
from unified_skill_fixture import unified_host


def test_repeated_original_host_lifecycle(tmp_path):
    source = Path(__file__).resolve().parents[2]
    output = Path(os.environ["PRODUCTION_RESOURCE_REPORT"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "RUNNING",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip()
        ),
        "before_loop": process_metrics(),
        "cycles": [],
        "boundaries": "Original Python services/SQLite/Trace/MCP with synthetic model/ASR/PCM. Not a real provider, physical audio, or installed Electron test; original installed GUI is separate.",
    }
    files = [
        *sorted((source / "backend/app").rglob("*.py")),
        Path(__file__),
        source / "backend/tests/hardening_soak_workload.py",
        source / "backend/tests/unified_skill_fixture.py",
        source / "backend/tests/workflow_fixture.py",
        source / "backend/tests/scheduler_fixture.py",
        source / "desktop/package.json",
        source / "desktop/electron/build-info.json",
    ]
    report["source_files"] = {
        file.relative_to(source).as_posix(): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in files
    }

    async def scenario():
        for index in range(3):
            root = tmp_path / str(index)
            root.mkdir()
            started = time.monotonic()
            async with unified_host(root) as h:
                assert (await h.agent())[1]["tools"]
                await voice_cycle(h, h.owner)
                tool = await h.connect_mcp()
                manager = h.app.state.mcp_connections
                child = manager.connections[tool["server_id"]].transport.process
                reply = await manager.invoke(
                    tool["server_id"], "call_tool", "echo", {"text": "owned lifecycle test"}
                )
                assert reply.content[0].text == "owned lifecycle test"
                await manager.remove(tool["server_id"])
                receipt = {"pid": child.pid, "exit_code": child.returncode}
                assert child.returncode == 0
                del child
                assert await h.trace.flush()
                assert not any(live_resources(h).values())
                running = process_metrics()
            await asyncio.sleep(0)
            gc.collect()
            closed = {
                **process_metrics(),
                "live_resources": live_resources(h),
                "journal_closed": h.trace.journal.connection is None,
                "writer_done": h.trace.worker.done(),
                "pool_retention": pool_retention(h),
            }
            assert not any(closed["live_resources"].values())
            assert closed["journal_closed"] and closed["writer_done"]
            report["cycles"].append(
                {
                    "cycle": index,
                    "seconds": time.monotonic() - started,
                    "mcp": receipt,
                    "running": running,
                    "closed": closed,
                }
            )
            del h
        report["idle_after_shutdown"] = []
        for _ in range(7):
            gc.collect()
            report["idle_after_shutdown"].append(process_metrics())
            if len(report["idle_after_shutdown"]) < 7:
                await asyncio.sleep(5)

    try:
        asyncio.run(scenario())
        gc.collect()
        report["after_loop"] = process_metrics()
        # Closing the actual loop joins its shared thread executor; measuring
        # inside the loop alone would count intentional warm worker threads.
        assert report["after_loop"]["python_threads"] == report["before_loop"]["python_threads"]
        assert len(report["cycles"]) == 3 and threading.active_count() >= 1
        report["status"] = "PASS"
    except BaseException as error:
        report.update(status="FAIL", failure_type=type(error).__name__)
        raise
    finally:
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output)

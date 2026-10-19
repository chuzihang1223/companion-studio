"""Independent Skill08 adversarial tests. All state is confined to a new cache profile."""

import asyncio
from pathlib import Path
import sys
import threading

import pytest

from scheduler_fixture import scheduler_host, scheduled, run_to_completion
from workflow_fixture import graph, settle
from app.database.session import engine
from app.services.knowledge.service import KnowledgeService
from app.services.knowledge.contracts import CreateCollection
from app.services.knowledge.context import GroundedService, model_target
from app.services.recovery.execution import RecoveryExecution
from app.services.scheduling.contracts import ScheduleError, Trigger
from app.services.scheduling.conditions import metadata_snapshot, scope_identity
from app.services.scheduling.directory_pin import pin_directory
from app.services.workflows.contracts import RetryPolicy


async def admit(scheduler, definition, owner):
    scheduler.save(definition)
    row = scheduler.run_now(definition.schedule_id, owner)
    await scheduler.tick()
    current = scheduler.store.occurrence(row["occurrence_id"], owner)
    if current["status"] == "WAITING_PERMISSION":
        scheduler.grant_once(row["occurrence_id"], owner)
        await scheduler.tick()
    return scheduler.store.occurrence(row["occurrence_id"], owner)


def test_object_ref_search_arguments_cannot_bypass_keyword_policy(tmp_path):
    class FakeRemoteEmbedding:
        mode = "REMOTE"
        dimension = 2
        fingerprint = "f" * 64
        calls = 0

        def embed(self, texts):
            self.calls += 1
            return [[1.0, 0.0] for _ in texts]

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            knowledge = app.state.knowledge = KnowledgeService(engine)
            try:
                collection = knowledge.store.create(
                    CreateCollection(name="Synthetic no private data")
                )
                knowledge.store.grant(
                    owner, "assistant", [collection["id"]], [model_target(app.state.llm_service)]
                )
                fake = FakeRemoteEmbedding()
                knowledge.providers[collection["id"]] = fake
                workflow = graph(
                    "tool",
                    {"name": "knowledge.search", "arguments": {"$ref": "inputs.search_args"}},
                )
                workflow.input_schema = {
                    "type": "object",
                    "properties": {"search_args": {"type": "object"}},
                    "additionalProperties": False,
                }
                definition = scheduled(
                    app,
                    owner,
                    clock,
                    graph_definition=workflow,
                    inputs={
                        "search_args": {
                            "query": "SYNTHETIC_QUERY",
                            "collection_ids": [collection["id"]],
                            "mode": "hybrid",
                        }
                    },
                )
                scheduler.save(definition)
                row = scheduler.run_now(definition.schedule_id, owner)
                await scheduler.tick()
                current = scheduler.store.occurrence(row["occurrence_id"], owner)
                if current["workflow_run_id"]:
                    await run_to_completion(app, scheduler, owner, row["occurrence_id"])
                assert fake.calls == 0, (
                    "Dynamic whole-object arguments dispatched unauthorized synthetic remote embedding"
                )
            finally:
                await knowledge.close()

    asyncio.run(run())


def test_expiry_after_checkpoint_io_blocks_actual_operation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.permission_policy.read_preferences", lambda: {"permission_mode": "smart"}
    )

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered, release = asyncio.Event(), asyncio.Event()
            original = RecoveryExecution.before
            effects = []

            async def delayed(self, call, index, allowed):
                await original(self, call, index, allowed)
                entered.set()
                await release.wait()

            async def desktop(operation, *args, **kwargs):
                effects.append(operation)
                return {"verified": True, "running": True}

            monkeypatch.setattr(RecoveryExecution, "before", delayed)
            monkeypatch.setattr(app.state.desktop_bridge, "request", desktop)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "tool", {"name": "app.launch", "arguments": {"app": "notepad"}}
                ),
            )
            row = await admit(scheduler, definition, owner)
            waiting = await settle(app, owner, row["workflow_run_id"], waiting=True)
            pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
            app.state.workflows.approve(row["workflow_run_id"], owner, pending["approval_id"], True)
            await asyncio.wait_for(entered.wait(), 5)
            clock.advance(301)
            await (
                scheduler.tick()
            )  # renew legitimate leader; test permission, not an expired leader
            release.set()
            result = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert not effects
            assert result["status"] != "COMPLETED"

    asyncio.run(run())


def test_source_revoke_after_checkpoint_blocks_read_dispatch(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            entered, release = asyncio.Event(), asyncio.Event()
            original = RecoveryExecution.before
            operations = []

            async def delayed(self, call, index, allowed):
                await original(self, call, index, allowed)
                entered.set()
                await release.wait()

            from app.services.coding.workspace import CodingWorkspaceExecutor

            def synthetic_execute(self, call):
                operations.append(call["name"])
                return {"verified": True, "content": "synthetic"}

            monkeypatch.setattr(RecoveryExecution, "before", delayed)
            monkeypatch.setattr(CodingWorkspaceExecutor, "execute", synthetic_execute)
            source = scheduler.watch.register(owner, "FILE_CHANGE", clock())
            definition = scheduled(
                app,
                owner,
                clock,
                "CONDITION",
                graph_definition=graph(
                    "tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}
                ),
                trigger_config=Trigger(condition_type="FILE_CHANGE", source_id=source["source_id"]),
            )
            row = await admit(scheduler, definition, owner)
            await asyncio.wait_for(entered.wait(), 5)
            scheduler.watch.revoke(source["source_id"], owner)
            release.set()
            result = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert not operations
            assert result["status"] != "COMPLETED"

    asyncio.run(run())


def test_retry_does_not_dispatch_after_source_revoked(tmp_path, monkeypatch):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            from app.services.coding.workspace import CodingWorkspaceExecutor

            source = scheduler.watch.register(owner, "FILE_CHANGE", clock())
            calls = []

            def failed(self, call):
                calls.append(call["name"])
                scheduler.watch.revoke(source["source_id"], owner)
                raise TimeoutError("synthetic transient read failure")

            monkeypatch.setattr(CodingWorkspaceExecutor, "execute", failed)
            workflow = graph(
                "tool", {"name": "workspace.read", "arguments": {"path": "fixture.txt"}}
            )
            workflow.nodes[1].retry_policy = RetryPolicy(strategy="safe_read", max_attempts=3)
            definition = scheduled(
                app,
                owner,
                clock,
                "CONDITION",
                graph_definition=workflow,
                trigger_config=Trigger(condition_type="FILE_CHANGE", source_id=source["source_id"]),
            )
            row = await admit(scheduler, definition, owner)
            result = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert len(calls) == 1
            assert result["status"] != "COMPLETED"

    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
def test_grounded_dispatch_guard_runs_after_async_prompt_barrier(tmp_path, stream):
    class SyntheticModel:
        calls = 0
        config = None

        async def chat(self, messages):
            self.calls += 1
            return "synthetic"

        async def stream_chat(self, messages):
            self.calls += 1
            yield "synthetic"

    async def run():
        started, release = threading.Event(), threading.Event()
        model = SyntheticModel()
        grounded = GroundedService(None, model, [], "owner", "assistant", None)
        grounded.results = [{}]
        revoked = False

        def prompt(messages):
            started.set()
            if not release.wait(5):
                raise RuntimeError("fixture barrier timeout")
            return messages

        def guard():
            if revoked:
                raise ScheduleError("SCHEDULE_PERMISSION_REQUIRED")

        grounded.prompt = prompt
        grounded.dispatch_guard = guard

        async def invoke():
            if stream:
                return [
                    item
                    async for item in grounded.stream_chat(
                        [{"role": "user", "content": "synthetic"}]
                    )
                ]
            return await grounded.chat([{"role": "user", "content": "synthetic"}])

        task = asyncio.create_task(invoke())
        assert await asyncio.to_thread(started.wait, 5)
        revoked = True
        release.set()
        with pytest.raises(ScheduleError):
            await task
        assert model.calls == 0

    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows kernel directory sharing semantics")
def test_directory_handle_pin_prevents_replace_and_secret_metadata_does_not_trigger(tmp_path):
    root = tmp_path / "selected"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "ordinary.txt").write_text("synthetic")
    secret = root / ".env"
    secret.write_text("SYNTHETIC_NON_SECRET=one")
    record = scope_identity(str(root))
    before = metadata_snapshot(record)
    secret.write_text("SYNTHETIC_NON_SECRET=changed-value")
    assert before == metadata_snapshot(record)
    with pin_directory(child):
        with pytest.raises(OSError):
            child.rename(root / "replaced")
    assert child.is_dir()


def test_stale_mcp_schema_never_reaches_adapter(tmp_path, monkeypatch):
    from test_mcp_connection import config
    from app.services.mcp_ext.contracts import digest

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            connection = app.state.mcp_connections
            cfg = config()
            server = connection.save(cfg)
            await connection.connect(server, approved_fingerprint=digest(cfg.model_dump()))
            registry = app.state.external_tools
            await registry.discover(server)
            tool = next(item for item in registry.listing(server) if item["name"] == "echo")
            registry.configure(tool["id"], True, "READ_ONLY_LOCAL")
            effects = []

            async def adapter(call):
                effects.append(call)
                return {"verified": True}

            monkeypatch.setattr(app.state.mcp_adapter, "execute", adapter)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "mcp_tool",
                    {
                        "tool_id": tool["id"],
                        "server_id": server,
                        "schema_fingerprint": tool["schema_fingerprint"],
                        "arguments": {"text": "synthetic"},
                    },
                ),
            )
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["status"]
                == "WAITING_PERMISSION"
            )
            scheduler.grant_once(row["occurrence_id"], owner)
            registry.tools[tool["id"]]["schema_fingerprint"] = "a" * 64
            await scheduler.tick()
            checked = scheduler.store.occurrence(row["occurrence_id"], owner)
            assert checked["workflow_run_id"] is None
            assert not effects

    asyncio.run(run())


def test_original_tool_permission_wait_rechecks_schedule_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.permission_policy.read_preferences", lambda: {"permission_mode": "ask"}
    )

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            effects = []

            async def desktop(operation, *args, **kwargs):
                effects.append(operation)
                return {"verified": True, "running": True}

            monkeypatch.setattr(app.state.desktop_bridge, "request", desktop)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "tool", {"name": "app.launch", "arguments": {"app": "notepad"}}
                ),
            )
            row = await admit(scheduler, definition, owner)
            waiting = await settle(app, owner, row["workflow_run_id"], waiting=True)
            assert waiting["state"] == "WAITING_PERMISSION"
            pending = next(item["pending"] for item in waiting["nodes"] if item.get("pending"))
            clock.advance(301)
            await scheduler.tick()
            app.state.workflows.approve(row["workflow_run_id"], owner, pending["approval_id"], True)
            result = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert not effects
            assert result["status"] != "COMPLETED"

    asyncio.run(run())


def test_schedule_consent_does_not_grant_original_mcp_permission(tmp_path, monkeypatch):
    from test_mcp_connection import config
    from app.services.mcp_ext.contracts import digest

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            connection = app.state.mcp_connections
            cfg = config()
            server = connection.save(cfg)
            await connection.connect(server, approved_fingerprint=digest(cfg.model_dump()))
            registry = app.state.external_tools
            await registry.discover(server)
            tool = next(item for item in registry.listing(server) if item["name"] == "echo")
            registry.configure(tool["id"], True, "READ_ONLY_LOCAL")
            effects = []

            async def adapter(call):
                effects.append(call)
                return {"verified": True}

            monkeypatch.setattr(app.state.mcp_adapter, "execute", adapter)
            definition = scheduled(
                app,
                owner,
                clock,
                graph_definition=graph(
                    "mcp_tool",
                    {
                        "tool_id": tool["id"],
                        "server_id": server,
                        "schema_fingerprint": tool["schema_fingerprint"],
                        "arguments": {"text": "synthetic"},
                    },
                ),
            )
            row = await admit(scheduler, definition, owner)
            waiting = await settle(app, owner, row["workflow_run_id"], waiting=True)
            pending = next(item["pending"] for item in waiting["nodes"] if item.get("pending"))
            app.state.workflows.approve(row["workflow_run_id"], owner, pending["approval_id"], True)
            result = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            assert not effects
            assert result["status"] != "COMPLETED"
            assert not connection.connections[server].history

    asyncio.run(run())

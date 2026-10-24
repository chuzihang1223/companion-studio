"""Real owned stdio lifecycles: bounded retention and deletion/connect races."""

import asyncio

import pytest

from app.services.mcp_ext.contracts import MCPError, digest
from test_mcp_connection import config, host


def test_removed_servers_release_cached_lifecycles_and_owned_children(tmp_path):
    async def run():
        manager = host(tmp_path)
        children = []
        try:
            for index in range(4):
                cfg = config(display_name=f"Owned lifecycle {index}")
                key = manager.save(cfg)
                await manager.connect(key, approved_fingerprint=digest(cfg.model_dump()))
                children.append(manager.connections[key].transport.process)
                await manager.remove(key)
                assert not manager.store.listing("server")
                assert not manager.connections and not manager.locks and not manager.lock_users
                assert children[-1].returncode is not None
        finally:
            await manager.close()
        assert all(child.returncode is not None for child in children)

    asyncio.run(run())


def test_queued_remove_cannot_be_overtaken_by_reconnect(tmp_path):
    async def run():
        manager = host(tmp_path)
        cfg = config()
        removed = manager.save(cfg)
        other = manager.save(config(display_name="Other owned server"))
        entered, release = asyncio.Event(), asyncio.Event()

        async def discovery(server_id, _client):
            if server_id == removed:
                entered.set()
                await release.wait()

        manager.on_discovery = discovery
        tasks = []
        try:
            await manager.connect(
                other, approved_fingerprint=digest(manager.config(other).model_dump())
            )
            first = asyncio.create_task(
                manager.connect(removed, approved_fingerprint=digest(cfg.model_dump()))
            )
            tasks.append(first)
            await asyncio.wait_for(entered.wait(), 10)
            child = manager.connections[removed].transport.process
            deletion = asyncio.create_task(manager.remove(removed))
            tasks.append(deletion)
            await asyncio.sleep(0)  # register this lifecycle waiter before reconnect
            again = asyncio.create_task(
                manager.connect(removed, approved_fingerprint=digest(cfg.model_dump()))
            )
            tasks.append(again)
            await asyncio.sleep(0)
            assert not deletion.done() and not again.done()
            release.set()
            await first
            await deletion
            with pytest.raises(MCPError):
                await again
            assert removed not in manager.connections and removed not in manager.locks
            assert not manager.lock_users and child.returncode is not None
            reply = await manager.invoke(other, "call_tool", "echo", {"text": "still isolated"})
            assert reply.content[0].text == "still isolated"
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            await manager.close()

    asyncio.run(run())


def test_missing_ids_and_cancelled_waiters_do_not_leave_locks(tmp_path):
    async def run():
        manager = host(tmp_path)
        for index in range(20):
            with pytest.raises(MCPError):
                await manager.connect(f"missing-{index}", approved_fingerprint="unapproved")
        entered, release = asyncio.Event(), asyncio.Event()

        async def owner():
            async with manager.lifecycle("owned-cancellation"):
                entered.set()
                await release.wait()

        first = asyncio.create_task(owner())
        await entered.wait()
        second = asyncio.create_task(manager.disconnect("owned-cancellation"))
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        release.set()
        await first
        assert not manager.locks and not manager.lock_users and not manager.connections
        await manager.close()

    asyncio.run(run())


def test_original_registry_releases_deleted_server_tool_generations(tmp_path):
    from unified_skill_fixture import unified_host

    async def run():
        async with unified_host(tmp_path) as h:
            for _ in range(3):
                tool = await h.connect_mcp()
                assert h.app.state.external_tools.tools
                await h.app.state.mcp_connections.remove(tool["server_id"])
                assert not h.app.state.external_tools.tools
                assert not h.app.state.external_tools.listing()

    asyncio.run(run())

"""任务队列的归属、去重和配额回归，不依赖模型或桌面程序。"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.models.runtime_task import RuntimeTask
from app.services import runtime_registry


@pytest.fixture
def registry_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'registry.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(runtime_registry, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_same_conversation_waiter_cancellation_preserves_owner_resources(registry_factory):
    async def scenario():
        registry = runtime_registry.RuntimeTaskRegistry()
        entered, release = asyncio.Event(), asyncio.Event()
        second_entered = False

        async def first():
            async with registry.run("first", "conversation", "first-session"):
                entered.set()
                await release.wait()

        async def second():
            nonlocal second_entered
            async with registry.run("second", "conversation", "second-session"):
                second_entered = True

        owner = asyncio.create_task(first())
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(second())
        await asyncio.sleep(0)
        with registry_factory() as db:
            assert db.get(RuntimeTask, "second").status == "queued"
        assert not second_entered
        registry.cancel_session("second-session")
        await asyncio.gather(waiter, return_exceptions=True)
        assert not owner.done()
        assert registry.envelope("first-session")["task_id"] == "first"
        release.set()
        await owner
        with registry_factory() as db:
            assert db.get(RuntimeTask, "first").status == "completed"
            assert db.get(RuntimeTask, "second").status == "cancelled"
        assert not registry.active and not registry.by_session
        assert not registry.conversation_locks and registry.gates["agent"]._value == 3

    asyncio.run(scenario())


def test_duplicate_task_id_cannot_run_again_after_completion(registry_factory):
    async def scenario():
        registry = runtime_registry.RuntimeTaskRegistry()
        async with registry.run("same-id", "conversation"):
            with pytest.raises(ValueError, match="同一任务"):
                async with registry.run("same-id", "other-conversation"):
                    pytest.fail("Duplicate task entered")
        with pytest.raises(ValueError, match="已经执行过"):
            async with registry.run("same-id", "conversation"):
                pytest.fail("Completed task replayed")
        with registry_factory() as db:
            assert db.get(RuntimeTask, "same-id").status == "completed"
        assert not registry.active

    asyncio.run(scenario())


def test_different_conversations_share_the_agent_concurrency_limit(registry_factory):
    async def scenario():
        registry = runtime_registry.RuntimeTaskRegistry()
        quota_reached, release = asyncio.Event(), asyncio.Event()
        current = peak = 0

        async def worker(index):
            nonlocal current, peak
            async with registry.run(f"task-{index}", f"conversation-{index}"):
                current += 1
                peak = max(peak, current)
                if current == 3:
                    quota_reached.set()
                await release.wait()
                current -= 1

        tasks = [asyncio.create_task(worker(index)) for index in range(5)]
        await asyncio.wait_for(quota_reached.wait(), 1)
        assert current == 3
        release.set()
        await asyncio.gather(*tasks)
        assert peak == 3 and current == 0
        assert not registry.active and not registry.conversation_locks
        assert registry.gates["agent"]._value == 3

    asyncio.run(scenario())

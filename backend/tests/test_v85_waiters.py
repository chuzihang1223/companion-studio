import asyncio
import pytest
from app.services.desktop.bridge import DesktopBridge
from app.services.desktop.lifecycle import TaskHandle, TaskCancelled
from app.services.desktop.task_waiter import TaskWaiter
from app.services.agent_router import ConversationRouter


def test_abort_wakes_an_operation_without_an_external_reply():
    async def scenario():
        handle = TaskHandle()

        async def pending():
            await asyncio.Event().wait()

        task = asyncio.create_task(TaskWaiter.run(pending, handle, 10))
        await asyncio.sleep(0.01)
        handle.target_exited()
        with pytest.raises(TaskCancelled, match="APP_EXITED_BEFORE_READY"):
            await asyncio.wait_for(task, 0.2)

    asyncio.run(scenario())


def test_timeout_releases_bridge_even_when_queue_is_full():
    async def scenario():
        bridge = DesktopBridge()
        bridge.queue = asyncio.Queue(maxsize=1)
        await bridge.queue.put({"id": "stale"})
        with pytest.raises(TimeoutError):
            await bridge.request("app.launch", {}, "s", 0.02)
        assert not bridge.pending

    asyncio.run(scenario())


def test_task_waiter_timeout_and_cancellation_cleanup():
    async def scenario():
        cleaned = asyncio.Event()

        async def pending():
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with pytest.raises(TimeoutError):
            await TaskWaiter.run(pending, TaskHandle(), 0.01)
        assert cleaned.is_set()

    asyncio.run(scenario())


def test_local_actions_route_without_model_tool_calling():
    router = ConversationRouter()
    for text in ("查看系统状态", "打开QQ音乐", "启动网易云音乐", "打开一个没有专门适配的软件"):
        assert router.route(text, "agent") == "agent"
    assert router.route("你今天开心吗？", "agent") == "chat"

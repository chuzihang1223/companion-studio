import asyncio
from types import SimpleNamespace
import pytest
from app.services.desktop.bridge import DesktopBridge
from app.services.desktop.lifecycle import TaskHandle, TaskCancelled
from app.services.desktop.goal_verifier import GoalVerifier
from app.services.agent_tools import ToolRegistry, ToolExecutor
from app.services.agent_router import ConversationRouter


def test_launch_does_not_verify_playback_goal():
    verifier = GoalVerifier()
    assert not verifier.verify(
        "打开网易云音乐并播放远航星的告别",
        [{"name": "app.launch", "status": "done", "result": {"verified": True}}],
    )
    assert verifier.verify(
        "打开网易云音乐并播放远航星的告别",
        [
            {
                "name": "music.play",
                "status": "done",
                "result": {"verified": True, "title": "远航星的告别", "status": "playing"},
            }
        ],
    )


def test_bridge_waits_for_real_observation_and_rejects_unknown_reply():
    async def scenario():
        bridge = DesktopBridge()
        task = asyncio.create_task(bridge.request("app.launch", {"app": "netease"}, "s1"))
        rows = await bridge.poll()
        assert not task.done() and rows[0]["operation"] == "app.launch"
        bridge.resolve("wrong", {"verified": True}, None)
        assert not task.done()
        bridge.resolve(rows[0]["id"], {"verified": True, "window": {"pid": 123}}, None)
        assert (await task)["window"]["pid"] == 123
        bridge.close()

    asyncio.run(scenario())


def test_pause_and_cancel_gate_next_action():
    async def scenario():
        handle = TaskHandle()
        handle.control("pause")
        task = asyncio.create_task(handle.checkpoint())
        await asyncio.sleep(0.01)
        assert not task.done()
        handle.control("resume")
        await task
        handle.control("takeover")
        task = asyncio.create_task(handle.checkpoint())
        handle.control("cancel")
        with pytest.raises(TaskCancelled):
            await task

    asyncio.run(scenario())


def test_tool_arguments_and_secret_paths_are_checked_locally(tmp_path):
    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.parse_calls(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "music_play",
                            "arguments": '{"query":"x","provider":"arbitrary.exe"}',
                        }
                    }
                ]
            }
        )
    with pytest.raises(ValueError):
        ToolExecutor(tmp_path)._path(".env")


def test_site_task_route_and_ordinary_chat():
    router = ConversationRouter()
    assert router.route("在B站搜索鸣潮最新PV") == "agent"
    assert router.route("我喜欢在B站看视频") == "chat"
    assert router.route("打开网易云音乐并播放远航星的告别") == "agent"
    assert router.route("再次读取当前项目 readme.txt", "agent") == "agent"
    assert router.route("继续修改项目中的文件", "auto") == "agent"
    assert router.route("你今天开心吗？", "agent") == "chat"

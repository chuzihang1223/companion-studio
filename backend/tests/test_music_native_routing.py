"""Provider requests use the desktop client; local playback stays in the app."""

from app.services.agent_tools import ToolExecutor, ToolRegistry


def test_netease_request_queues_desktop_client(tmp_path):
    registry = ToolRegistry()
    tools = registry.schemas_for("打开网易云音乐")
    assert [tool["function"]["name"] for tool in tools] == ["app_launch"]

    executor = ToolExecutor(tmp_path)
    result = executor.execute({"name": "music.search", "arguments": {"provider": "netease", "query": "测试歌曲"}})
    assert result["action"] == "launch_app"
    assert result["app"] == "netease"
    assert "url" not in result

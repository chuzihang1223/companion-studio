import asyncio
from types import SimpleNamespace
from pathlib import Path
import pytest
from app.services.application_alias import ApplicationAliasResolver
from app.services.local_action_intent import LocalActionIntentResolver
from app.services.llm.model_roles import ModelRoleRouter, ExecutionModelUnavailable


def test_installed_aliases_ambiguity_and_not_command_input():
    resolver = ApplicationAliasResolver()
    records = [
        {"app_id": "a", "name": "Everything 1.4.1.1032 (x64)", "installed": True},
        {"app_id": "b", "name": "Visual Studio Code", "installed": True},
        {"app_id": "c", "name": "QQ音乐", "installed": True},
        {"app_id": "d", "name": "Visual Studio 2022", "installed": True},
        {"app_id": "e", "name": "Visual Studio 2019", "installed": True},
    ]
    assert resolver.unique(resolver.match("everything", records))["app_id"] == "a"
    assert resolver.unique(resolver.match("vscode", records))["app_id"] == "b"
    assert resolver.unique(resolver.match("QQ Music", records))["app_id"] == "c"
    assert resolver.unique(resolver.match("Visual Studio", records)) is None
    intent = LocalActionIntentResolver()
    assert intent.resolve("助手，帮我打开英雄联盟。").target == "英雄联盟"
    assert intent.resolve("打开任意索引软件，然后搜索歌曲。").complex
    assert intent.resolve("查看系统状态").operation == "system.get_status"
    assert intent.resolve("关闭QQ音乐").operation == "app.close"
    assert (
        intent.resolve("打开QQ音乐; powershell.exe -c kill") is not None
    )  # only the first safe app is parsed
    assert intent.resolve("运行 main.py") is None
    assert intent.resolve("运行 C:\\Windows\\system32\\cmd.exe") is None
    assert intent.resolve("打开 https://example.org").operation == "browser.open_url"


def test_role_selection_honest_observed_capability_and_hot_switch():
    text = SimpleNamespace(
        config=SimpleNamespace(capabilities={"tool_calling": False}, model="text-only")
    )

    class Tools:
        config = SimpleNamespace(capabilities={"tool_calling": None}, model="execution")

        async def plan_tools(self, messages, tools):
            return {"tool_calls": [{"id": "actual-http-result"}]}

    tool = Tools()
    app = SimpleNamespace(state=SimpleNamespace(llm_service=text))
    router = ModelRoleRouter(app)
    with pytest.raises(ExecutionModelUnavailable):
        router.service("tool_execution_model", tools=True)
    router.configure({"tool_execution_model": tool})
    assert router.service("character_model") is text
    assert router.service("tool_execution_model", tools=True) is tool
    asyncio.run(router.plan_tools([], []))
    capability = router.public()["tool_execution_model"]["capabilities"]["tool_calling"]
    assert capability == {"value": True, "source": "observed"}
    assert router.service("coding_model", tools=True) is tool
    next_character = SimpleNamespace(config=SimpleNamespace(capabilities={}, model="new-character"))
    app.state.llm_service = next_character
    assert router.service("character_model") is next_character


@pytest.mark.parametrize(
    "identity",
    [
        "Microsoft.Windows.ImmersiveControlPanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel",
        "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel",
    ],
)
def test_system_identity_precedes_a_third_party_settings_shortcut(identity):
    resolver = ApplicationAliasResolver()
    records = [
        {"app_id": "third-party", "name": "皮肤盒子", "aliases": ["设置"], "installed": True},
        {"app_id": "windows", "name": "设置", "app_user_model_id": identity, "installed": True},
    ]
    assert resolver.unique(resolver.match("Windows 设置", records))["app_id"] == "windows"
    assert resolver.unique(resolver.match("皮肤盒子", records))["app_id"] == "third-party"

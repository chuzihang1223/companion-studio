import pytest
from app.services.local_action_intent import LocalActionIntentResolver
from app.services.web_action import WebActionResolver


@pytest.mark.parametrize(
    "text,operation,target",
    [
        ("打开QQ音乐播放周杰伦的歌", "music.play", "QQ音乐"),
        ("打开网易云音乐播放周杰伦的歌曲", "music.play", "网易云音乐"),
        ("打开B站搜索鸣潮", "browser.search_site", "B站"),
        ("打开GitHub搜索electron agent", "browser.search_site", "GitHub"),
        ("打开百度", "browser.open_url", "百度"),
        ("打开python.org搜索asyncio", "browser.search_site", "python.org"),
        ("打开随机中文软件", "app.launch", "随机中文软件"),
    ],
)
def test_deterministic_routing(text, operation, target):
    intent = LocalActionIntentResolver().resolve(text)
    assert intent.operation == operation and intent.target == target and not intent.complex


def test_suspicious_website_target_rejected():
    for text in [
        "打开https://secret:password@example.com",
        "打开example.com;whoami",
        "打开demo.py",
    ]:
        assert WebActionResolver().resolve(text) is None


def test_site_alias_domains():
    r = WebActionResolver()
    assert r.resolve("打开bilibili.com搜索鸣潮").arguments["site"] == "bilibili"
    assert (
        r.resolve("打开https://github.com搜索electron agent").arguments["query"] == "electron agent"
    )

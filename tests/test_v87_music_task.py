from types import SimpleNamespace
import asyncio
from uuid import uuid4
from app.services.local_action_intent import LocalActionIntentResolver
from app.services.desktop.music_task import music_context, music_feedback
from app.services.desktop.runtime import AgentTaskRuntime
from app.services.desktop.lifecycle import TaskHandle


def test_punctuated_command_is_one_music_task():
    intent = LocalActionIntentResolver().resolve("打开QQ音乐，播放《再于终末归还》。")
    assert intent.operation == "music.play"
    assert intent.arguments == {"provider": "qqmusic", "query": "《再于终末归还》"}
    assert not intent.complex


def test_platform_context_and_natural_failure():
    call = {"name": "music.play", "arguments": {"provider": "qqmusic", "query": "歌曲"}}
    qq = music_context(SimpleNamespace(task_id="qq-task"), "qqmusic", "《歌曲》 歌手")
    net = music_context(SimpleNamespace(task_id="net-task"), "netease", "另一首")
    assert qq["taskId"] != net["taskId"]
    assert qq["applicationName"] == "QQ音乐"
    assert qq["targetSong"] == "歌曲" and qq["targetArtist"] == "歌手"
    assert "网易云" not in music_feedback(
        call, {"music_context": net, "public_error": "网易云失败"}, True
    )
    assert "window_ocr" not in music_feedback(
        call, {"music_context": qq, "error": "window_ocr timeout"}, True
    )
    assert "QQ音乐" in music_feedback(
        call, {"music_context": qq, "verified": True, "title": "歌曲"}
    )


def test_adapter_failure_is_not_repeated_or_stripped():
    asyncio.run(check_adapter_failure())


async def check_adapter_failure():
    work = SimpleNamespace(step_status=lambda *a: None, emit=lambda *a, **kw: None)
    runtime = AgentTaskRuntime(SimpleNamespace(state=SimpleNamespace(work_state=work)))
    context = SimpleNamespace(work_session_id="work", task_id=str(uuid4()))
    count = 0

    async def operation():
        nonlocal count
        count += 1
        return {
            "verified": False,
            "error": "classified QQ search failed",
            "music_context": {"providerId": "qqmusic"},
            "public_error": "QQ音乐没找到",
        }

    result = await runtime.step(context, "搜索歌曲", operation, TaskHandle(), max_attempts=1)
    assert count == 1
    assert result["music_context"]["providerId"] == "qqmusic"
    assert result["public_error"] == "QQ音乐没找到"


def test_music_stream_has_one_canonical_reply_and_task_identity(tmp_path, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database.session import Base
    from app.main import app
    from app.services import agent_service, work_state
    from app.api import agent as agent_api

    engine = create_engine(
        f"sqlite:///{(tmp_path / 'stream.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (agent_service, work_state, agent_api):
        monkeypatch.setattr(module, "SessionLocal", factory)
    from app import main as application
    from app.services import runtime_registry
    from app.services.recovery.checkpoints import CheckpointManager
    from app.services.trace.store import TraceStore

    monkeypatch.setattr(runtime_registry, "SessionLocal", factory)
    monkeypatch.setattr(application, "CheckpointManager", lambda: CheckpointManager(factory))
    monkeypatch.setattr(application, "TraceStore", lambda: TraceStore(factory))

    class NoModelRequests:
        async def chat(self, *args):
            raise AssertionError("A deterministic music reply must not call a model")

    async def desktop(operation, arguments, session, **kwargs):
        return {
            "verified": True,
            "installed": True,
            "window": {"handle": 1},
            "title": "再于终末归还",
            "artist": "鸣潮先约电台",
            "status": "playing",
            "app": arguments.get("app"),
            "music_context": arguments.get("music_context"),
        }

    with TestClient(app) as client:
        app.state.llm_service = NoModelRequests()
        monkeypatch.setattr(app.state.desktop_bridge, "request", desktop)
        plan = client.post(
            "/api/agent/plan",
            json={"character_id": "assistant", "message": "打开QQ音乐，播放《再于终末归还》。"},
        ).json()
        result = client.post(
            "/api/agent/execute/stream",
            json={
                "plan_id": plan["plan_id"],
                "approved_ids": [plan["calls"][0]["id"]],
                "voice_enabled": False,
            },
        )
        assert result.status_code == 200
        events = []
        for block in result.text.split("\n\n"):
            data = next(
                (line[6:] for line in block.splitlines() if line.startswith("data: ")), None
            )
            if data:
                events.append(json.loads(data))
        assert not any(e.get("event_type") == "error" for e in events)
        texts = [e["text"] for e in events if e.get("event_type") == "text_delta"]
        completed = next(e for e in events if e.get("event_type") == "completed")
        assert "".join(texts) == completed["content"] == "《再于终末归还》已经在QQ音乐里放好啦。"
        identity = completed["tools"][0]["result"]["music_context"]
        assert identity["taskId"] == plan["plan_id"] and identity["applicationId"] == "qqmusic"
        work = client.get("/api/work/" + plan["work_session_id"]).json()
        bound = [
            e["detail"]["music_context"] for e in work["events"] if "music_context" in e["detail"]
        ]
        assert len(bound) >= 10 and all(
            e["taskId"] == plan["plan_id"] and e["providerId"] == "qqmusic" for e in bound
        )
    engine.dispose()

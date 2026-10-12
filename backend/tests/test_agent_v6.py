import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.main import app
from app.models import Conversation, Message
from app.services.agent_router import ConversationRouter
from app.services.agent_tools import PermissionManager, ToolExecutor, ToolRegistry
from app.services.music_library import LocalMusicLibrary
from app.services import agent_service as agent_module
from app.services import work_state as work_module


def test_intent_and_workspace_boundaries(tmp_path):
    router = ConversationRouter()
    assert router.route("我今天好累") == "chat"
    assert router.route("帮我打开 GitHub") == "agent"
    executor = ToolExecutor(tmp_path / "workspace")
    with pytest.raises(ValueError):
        executor.execute({"name": "code.create_file", "arguments": {"path": "../escape.py", "content": "x"}})
    with pytest.raises(ValueError):
        executor.execute({"name": "code.create_file", "arguments": {"path": "C:\\escape.py", "content": "x"}})
    call = {"name": "code.create_file", "arguments": {"path": "demo/hello.py", "content": "print('hello')"}}
    created = executor.execute(call)
    assert created["bytes"] == 14 and "+print('hello')" in created["diff"]
    with pytest.raises(FileExistsError):
        executor.execute(call)
    edited = executor.execute({"name": "code.edit_file", "arguments":
                               {"path": "demo/hello.py", "content": "print('hi')"}})
    assert "-print('hello')" in edited["diff"] and "+print('hi')" in edited["diff"]
    output = executor.execute({"name": "code.run", "arguments": {"path": "demo/hello.py"}})
    assert output["exit_code"] == 0 and "hi" in output["output"]
    assert not PermissionManager.permitted({"id": "run1", "requires_confirmation": True}, set())


def test_tool_schema_and_safe_browser_handoff(tmp_path):
    registry = ToolRegistry()
    item = {"id": "call-1", "function": {"name": "browser_open_url", "arguments": json.dumps({"url": "https://github.com"})}}
    parsed = registry.parse_calls({"tool_calls": [item]})
    result = ToolExecutor(tmp_path / "workspace").execute(parsed[0])
    assert result["action"] == "open_url" and result["url"] == "https://github.com"
    with pytest.raises(ValueError):
        registry.parse_calls({"tool_calls": [{"function": {"name": "shell_delete", "arguments": "{}"}}]})
    close = registry.parse_calls({"tool_calls": [{"id": "app-1", "function": {
        "name": "app_close", "arguments": '{"app":"notepad"}'}}]})[0]
    assert close["requires_confirmation"] and close["risk_level"] == "high"
    assert ToolExecutor(tmp_path / "workspace").execute(close)["action"] == "close_app"


class FakeToolLLM:
    async def plan_tools(self, messages, tools):
        assert "ROLE\n" in messages[0]["content"]
        assert "通用助手" in messages[0]["content"]
        assert any(row["function"]["name"] == "code_create_file" for row in tools)
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "code_create_file", "arguments": json.dumps({"path": "hello.py", "content": "print('你好')"}, ensure_ascii=False)}}]}

    async def chat(self, messages):
        assert any(row.get("role") == "tool" and "hello.py" in row.get("content", "") for row in messages)
        return "写好了，我把小例子放在工作区啦。"


def test_agent_plan_execute_persists_character_reply(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'agent.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(agent_module, "SessionLocal", factory)
    monkeypatch.setattr(work_module, "SessionLocal", factory)
    from app.services.coding import documents
    monkeypatch.setattr(documents, 'SessionLocal', factory)
    with TestClient(app) as client:
        app.state.llm_service = FakeToolLLM()
        app.state.agent_service.executor = ToolExecutor(tmp_path / "workspace")
        response = client.post("/api/agent/plan", json={"character_id": "assistant", "message": "帮我写一个 Python 示例"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["calls"][0]["name"] == "code.create_file"
        result = client.post("/api/agent/execute", json={"plan_id": data["plan_id"], "approved_ids": [data['calls'][0]['id']], "voice_enabled": False})
        assert result.status_code == 200, result.text
        assert result.json()["tools"][0]["status"] == "done"
        assert not (tmp_path / 'workspace' / 'hello.py').exists()
        doc_id=result.json()['tools'][0]['result']['document_id']
        assert documents.VirtualDocumentStore().get(data['work_session_id'],doc_id)['content']=="print('你好')"
        with factory() as db:
            rows = db.scalars(select(Message).order_by(Message.created_at)).all()
            assert [row.role for row in rows] == ["user", "assistant"]
            assert "写好了" in rows[1].content
            assert db.get(Conversation, result.json()["conversation_id"]) is not None
    engine.dispose()


def test_selected_work_folder_scopes_agent_write(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'selected.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(agent_module, "SessionLocal", factory)
    monkeypatch.setattr(work_module, "SessionLocal", factory)
    from app.services.coding import documents
    monkeypatch.setattr(documents, 'SessionLocal', factory)
    monkeypatch.setenv('COMPANION_CONTROL_TOKEN', 'selected-workspace-test')
    from app.services import permission_policy
    monkeypatch.setattr(permission_policy, 'read_preferences', lambda: {'permission_mode': 'ask'})
    selected = tmp_path / "chosen"
    selected.mkdir()
    with TestClient(app) as client:
        app.state.llm_service = FakeToolLLM()
        app.state.agent_service.executor = ToolExecutor(tmp_path / "default")
        with factory() as db:
            conversation = Conversation(character_id='assistant', conversation_type='work', preferred_mode='work')
            db.add(conversation)
            db.commit()
            conversation_id = conversation.id
        choice = client.post('/api/work/workspace', json={'conversation_id': conversation_id, 'root': str(selected)},
                             headers={'x-companion-control-token': 'selected-workspace-test'})
        assert choice.status_code == 200, choice.text
        plan = client.post("/api/agent/plan", json={"character_id": "assistant", 'conversation_id': conversation_id,
            "message": "帮我写一个 Python 示例"}).json()
        assert plan["calls"][0]["requires_confirmation"]
        result = client.post("/api/agent/execute", json={"plan_id": plan["plan_id"],
            "approved_ids": [plan["calls"][0]["id"]], "voice_enabled": False})
        assert result.status_code == 200, result.text
        assert not (selected / 'hello.py').exists(), 'Selecting a folder must not imply write intent'
        doc_id=result.json()['tools'][0]['result']['document_id']
        assert documents.VirtualDocumentStore().get(plan['work_session_id'],doc_id)['content']=="print('你好')"
        assert not (tmp_path / "default" / "hello.py").exists()
    engine.dispose()


def test_local_music_uses_only_selected_folder(tmp_path):
    library = LocalMusicLibrary()
    library.settings_file = tmp_path / "music.json"
    music = tmp_path / "songs"
    music.mkdir()
    (music / "example.wav").write_bytes(b"RIFF" + b"\0" * 48)
    (music / "ignore.txt").write_text("not music")
    songs = library.choose(str(music))
    assert len(songs) == 1 and songs[0]["format"] == "wav"
    assert library.path(songs[0]["id"]) == music / "example.wav"
    assert library.path("0" * 24) is None

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.database.session import Base, get_db
from app.main import app
from app.models import Conversation, Message


def test_legacy_messages_survive_additive_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY, character_id VARCHAR(100) NOT NULL, "
                   "role VARCHAR(20) NOT NULL, content TEXT NOT NULL, created_at DATETIME NOT NULL)")
        db.execute("INSERT INTO messages VALUES ('old-1','assistant','user','旧聊天内容','2026-01-01')")
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    migrate(engine)
    migrate(engine)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT content FROM messages WHERE id='old-1'").fetchone() == ("旧聊天内容",)
        assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert db.execute("SELECT conversation_id FROM messages WHERE id='old-1'").fetchone()[0]
    engine.dispose()


def test_branch_keeps_parent_history_and_searches_message_content(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'branches.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        root = Conversation(character_id="assistant", title="第一次聊天")
        db.add(root)
        db.flush()
        db.add_all([
            Message(id="u1", character_id="assistant", conversation_id=root.id, role="user", content="今天天气怎么样"),
            Message(id="a1", character_id="assistant", conversation_id=root.id, role="assistant", content="一起看看窗外。"),
            Message(id="u2", character_id="assistant", conversation_id=root.id, role="user", content="我面试通过了"),
        ])
        db.commit()
        root_id = root.id

    def test_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = test_db
    try:
        with TestClient(app) as client:
            class FakeLLM:
                def __init__(self):
                    self.messages = []
                async def chat(self, prompt):
                    self.messages = prompt
                    return "我在这里，继续说给我听。"
            fake = FakeLLM()
            app.state.llm_service = fake
            branch = client.post(f"/api/conversations/{root_id}/branches", json={"fork_message_id": "a1"})
            assert branch.status_code == 200, branch.text
            branch_id = branch.json()["id"]
            assert [row["id"] for row in client.get(f"/api/conversations/{branch_id}/messages").json()] == ["u1", "a1"]
            assert [row["id"] for row in client.get(f"/api/conversations/{root_id}/messages").json()] == ["u1", "a1", "u2"]
            reply = client.post('/api/chat', json={
                'character_id': 'assistant', 'conversation_id': branch_id,
                'reply_to_message_id': 'a1', 'message': '我就是在回复你刚才那句。',
            })
            assert reply.status_code == 200, reply.text
            assert 'REPLY_TO_MESSAGE' in fake.messages[0]['content']
            assert '一起看看窗外' in fake.messages[0]['content']
            alternative = client.post('/api/chat/a1/retry')
            assert alternative.status_code == 200, alternative.text
            assert alternative.json()['message_id'] != 'a1'
            with factory() as db:
                assert db.get(Message, alternative.json()['message_id']).variant_of_message_id == 'a1'
            hits = client.get("/api/conversations", params={"character_id": "assistant", "search": "面试"}).json()
            assert [row["id"] for row in hits] == [root_id]
            assert client.patch(f"/api/conversations/{branch_id}", json={"title": "另一种聊天"}).json()["title"] == "另一种聊天"
            assert client.delete(f"/api/conversations/{root_id}").json() == {"status": "deleted"}
            assert [row["id"] for row in client.get(f"/api/conversations/{branch_id}/messages").json()][:2] == ["u1", "a1"]
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

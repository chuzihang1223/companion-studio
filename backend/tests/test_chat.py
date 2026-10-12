from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base, get_db
from app.main import app
from app.models.message import Message
from app.services.character.prompt_builder import CharacterPromptBuilder


def test_packaged_frontend_allows_streamed_audio_blobs():
    with TestClient(app) as client:
        response = client.get("/app/")
    if response.status_code == 200:
        assert "media-src 'self' blob:" in response.headers["content-security-policy"]


class FakeLLMService:
    def __init__(self):
        self.last_messages = []

    async def chat(self, messages):
        self.last_messages = messages
        return "累了就先歇一会儿，我在这里陪你。"


def test_character_prompt_uses_profile_and_context():
    with TestClient(app):
        character = app.state.characters.get("assistant")
        history = [{"role": "assistant", "content": "昨天聊过。"}]
        messages = CharacterPromptBuilder().build(character, history, "今天有点累")
    assert "通用助手" in messages[0]["content"]
    assert "不强制亲密" in messages[0]["content"]
    assert character.profile.identity == "" and character.profile.relationship == {}
    assert messages[1] == history[0]
    assert messages[-1] == {"role": "user", "content": "今天有点累"}


def test_chat_saves_and_loads_history_across_restart(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        session.add(Message(character_id="other_character", role="assistant", content="旧角色的聊天仍在。"))
        session.commit()

    def test_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = test_db
    fake = FakeLLMService()
    try:
        with TestClient(app) as client:
            app.state.llm_service = fake
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get("/api/character").json()["name"] == "通用助手"
            assert client.get("/api/chat/history").json() == []
            response = client.post("/api/chat", json={"character_id": "assistant", "message": "你好，今天有点累。"})
            assert response.status_code == 200, response.text
            assert response.json()["content"].startswith("累了")
            assert fake.last_messages[-1]["content"] == "你好，今天有点累。"
            assert client.post("/api/chat", json={"character_id": "assistant", "message": "x" * 4001}).status_code == 422
        with TestClient(app) as client:
            history = client.get("/api/chat/history").json()
            assert [item["role"] for item in history] == ["user", "assistant"]
            assert history[0]["content"] == "你好，今天有点累。"
            assert history[1]["content"] == response.json()["content"]
        with session_factory() as session:
            assert session.query(Message).filter_by(character_id="other_character").count() == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

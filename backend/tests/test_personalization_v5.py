from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import personalization as api
from app.database.session import Base
from app.main import app
from app.services.character.personalization import relevant_manual
from app.services.character.prompt_builder import CharacterPromptBuilder


def test_saved_overlay_and_manual_memory_take_effect_without_restart(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'profile.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    with TestClient(app) as client:
        original = client.get("/api/personalization/assistant").json()
        assert original["settings"]["reply_style"] == "natural"
        payload = {**original["settings"], "relationship": "我们是学习伙伴。", "appellation": "同学",
                   "personality": "更喜欢轻松地开玩笑。", "reply_style": "short"}
        assert client.put("/api/personalization/assistant", json=payload).status_code == 200
        created = client.post("/api/personalization/assistant/memories", json={"content": "我正在准备面试。"})
        assert created.status_code == 201
        memory_id = created.json()["id"]
        with factory() as db:
            manual = relevant_manual(db, "assistant", "你还记得我的面试吗？")
            assert relevant_manual(db, "assistant", "今天有点紧张。") == ["我正在准备面试。"]
        prompt = CharacterPromptBuilder().build(app.state.characters.get("assistant"), [], "你还记得我的面试吗？",
            personalization=client.get("/api/personalization/assistant").json()["settings"], manual_memory=manual)
        assert "我们是学习伙伴" in prompt[0]["content"]
        assert "我正在准备面试" in prompt[0]["content"]
        assert "同学" in prompt[0]["content"]
        affection = CharacterPromptBuilder().build(app.state.characters.get("assistant"), [], "我爱你。")
        assert "DIRECT_AFFECTION_RESPONSE" not in affection[0]["content"]
        assert client.patch(f"/api/personalization/assistant/memories/{memory_id}", json={"enabled": False}).status_code == 200
        with factory() as db:
            assert relevant_manual(db, "assistant", "面试") == []
        assert client.delete(f"/api/personalization/assistant/memories/{memory_id}").status_code == 204
        assert client.delete("/api/personalization/assistant/reset").status_code == 204
        after = client.get("/api/personalization/assistant").json()
        assert after["settings"]["relationship"] == ""
        assert after["manual_memories"] == []

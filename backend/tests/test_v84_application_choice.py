from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.main import app
from app.database.session import Base
from app.models import Message
from app.services import agent_service, agent_tools, work_state


def test_ambiguous_selection_preserves_message_and_rechecks_permission(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "choice.db"), connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(agent_service, "SessionLocal", factory)
    monkeypatch.setattr(work_state, "SessionLocal", factory)
    records = [
        {"app_id": "studio-a", "name": "Visual Studio 2019", "installed": True},
        {"app_id": "studio-b", "name": "Visual Studio 2022", "installed": True},
    ]
    monkeypatch.setattr(agent_service, "resolvable_applications", lambda: records)
    monkeypatch.setattr(agent_tools, "application_ids", lambda: ["studio-a", "studio-b"])
    monkeypatch.setattr(agent_service.trusted_actions, "allowed", lambda _action: False)

    class TextOnly:
        config = SimpleNamespace(
            capabilities={"tool_calling": False, "vision": False}, model="text"
        )

        async def plan_tools(self, *_):
            raise AssertionError("App ambiguity must not call a model")

    try:
        with TestClient(app) as client:
            app.state.llm_service = TextOnly()
            plan = client.post(
                "/api/agent/plan",
                json={"character_id": "assistant", "message": "打开 Visual Studio。"},
            ).json()
            assert plan["calls"] == [] and len(plan["local_resolution"]["candidates"]) == 2
            wrong = client.post(
                "/api/agent/select-application",
                json={"plan_id": plan["plan_id"], "app_id": "not-in-this-plan"},
            )
            assert wrong.status_code == 422
            # A renderer reload/restart restores the pending selection from persistent Work state.
            app.state.agent_service.pending.clear()
            selected = client.post(
                "/api/agent/select-application",
                json={"plan_id": plan["plan_id"], "app_id": "studio-b"},
            )
            assert selected.status_code == 200, selected.text
            call = selected.json()["calls"][0]
            assert call["arguments"]["app"] == "studio-b" and call["requires_confirmation"]
            assert call["action_id"].startswith("app.launch:")
            with factory() as db:
                assert db.query(Message).filter_by(role="user").count() == 1
    finally:
        engine.dispose()

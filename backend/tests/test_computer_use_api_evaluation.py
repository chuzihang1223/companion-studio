import asyncio
from uuid import uuid4
import httpx
from fastapi import FastAPI
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.api import computer_use
from app.database.migrations import migrate
from app.models import Conversation
from app.schemas.evaluation import RunRequest
from app.services.evaluation.engine import AgentEvaluationEngine
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.repository import EvaluationRepository


def test_private_routes_validate_owner_schema_and_fixture_profile(corpus, monkeypatch):
    store, _, owner = corpus
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "isolated-test-control")
    monkeypatch.setattr(computer_use, "SessionLocal", store.sessions)
    app = FastAPI()
    app.include_router(computer_use.router)
    payload = {"conversation_id": owner, "character_id": "assistant"}

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            assert (await client.post("/api/computer/owner", json=payload)).status_code == 403
            client.headers["X-Companion-Control-Token"] = "isolated-test-control"
            assert (
                await client.post("/api/computer/owner", json=payload)
            ).status_code == 404  # chat scope
            with store.sessions.begin() as db:
                db.get(Conversation, owner).conversation_type = "work"
            assert (await client.post("/api/computer/owner", json=payload)).status_code == 200
            assert (
                await client.post("/api/computer/owner", json=payload | {"character_id": "other"})
            ).status_code == 404
            assert (
                await client.post(
                    "/api/computer/owner", json=payload | {"conversation_id": str(uuid4())}
                )
            ).status_code == 404
            invalid = payload | {
                "operation": "click",
                "arguments": {"target_id": str(uuid4()), "x": 5, "script": "no"},
            }
            assert (await client.post("/api/computer/execute", json=invalid)).status_code == 422
            secret = "sk-" + "synthetic_only_" * 3
            rejected = await client.post(
                "/api/computer/execute",
                json=payload
                | {
                    "operation": "type_text",
                    "arguments": {
                        "target_id": str(uuid4()),
                        "observation_id": str(uuid4()),
                        "candidate_id": str(uuid4()),
                        "text": secret,
                    },
                },
            )
            assert rejected.status_code == 422 and secret not in rejected.text
            assert (
                await client.post(
                    "/api/computer/qa/fixture",
                    json=payload | {"consent_id": str(uuid4()), "operation": "open"},
                )
            ).status_code == 409
            with store.sessions.begin() as db:
                db.get(Conversation, owner).is_deleted = True
            assert (await client.post("/api/computer/owner", json=payload)).status_code == 404

    asyncio.run(run())


def test_24_native_cases_are_real_gui_only_and_no_paid_model():
    registry = BenchmarkRegistry("computer-use-core")
    assert len(registry.cases) == 24 and len({case.title for case in registry.cases}) == 24
    assert all(
        [mode.value for mode in case.execution_mode] == ["windows_gui"] for case in registry.cases
    )
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="computer-use-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )
    with pytest.raises(ValueError):
        RunRequest(dataset_id="computer-use-core", mode="windows_gui", concurrency=2)


def test_native_benchmark_without_isolated_profile_is_blocked_not_pass(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "evaluation.db").as_posix())
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    evaluator = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await evaluator.initialize()
        created = await evaluator.start(
            RunRequest(dataset_id="computer-use-core", mode="windows_gui"), FastAPI()
        )
        await asyncio.gather(*list(evaluator.tasks.values()))
        rows = repository.results(created["run_id"], 0, 100)["items"]
        assert len(rows) == 24 and all(row["execution_status"] == "BLOCKED" for row in rows)
        assert repository.detail(created["run_id"])["metrics"]["valid_count"] == 0
        assert not evaluator.environment.active
        await evaluator.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()

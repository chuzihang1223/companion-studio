import asyncio

from fastapi import FastAPI
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import Base
from app.schemas.evaluation import RunRequest
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.router_cases import component
from app.services.evaluation.router_fixtures import RouterFixture
from app.services.llm.model_roles import ModelRoleRouter
from app.services.llm.routing.budget import BudgetLedger
from app.services.llm.service import create_llm_service


def test_router_dataset_declares_forty_eight_unique_cases_and_no_live_budget():
    registry = BenchmarkRegistry("router-core")
    assert [row.case_id for row in registry.cases] == [
        f"ROUTE-{index:03}" for index in range(1, 49)
    ]
    assert registry.public()["case_count"] == len(registry.cases)
    with pytest.raises(ValueError):
        RunRequest(
            dataset_id="router-core",
            mode="live_model",
            live_consent=True,
            max_requests=1,
            token_budget=100,
        )


@pytest.mark.parametrize("index", range(1, 37), ids=lambda index: f"ROUTE-{index:03}")
def test_production_routing_component_benchmark(tmp_path, index):
    engine = create_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    Base.metadata.create_all(engine)
    app = FastAPI()
    app.state.llm_service = create_llm_service()
    app.state.model_roles = ModelRoleRouter(app)
    fixture = RouterFixture(app, BudgetLedger(sessionmaker(engine)))
    try:
        checks = asyncio.run(component(index, fixture))
        assert checks and all(value is True for value in checks.values()), checks
    finally:
        engine.dispose()


def test_router_benchmark_runs_through_original_worker_and_report(tmp_path):
    import json
    from app.database.migrations import migrate
    from app.services.evaluation.engine import AgentEvaluationEngine
    from app.services.evaluation.repository import EvaluationRepository
    from app.services.evaluation.reports import EvaluationReportService

    engine = create_engine(f"sqlite:///{(tmp_path / 'evaluation.db').as_posix()}")
    migrate(engine)
    repository = EvaluationRepository(sessionmaker(engine, expire_on_commit=False))
    runner = AgentEvaluationEngine(tmp_path / "attempts", repository)

    async def run():
        await runner.initialize()
        try:
            created = await runner.start(
                RunRequest(dataset_id="router-core", concurrency=2, timeout_seconds=30), FastAPI()
            )
            await asyncio.gather(*tuple(runner.tasks.values()))
            exported = EvaluationReportService(repository).export(created["run_id"], "json")[
                "content"
            ]
            (tmp_path / "router-result.json").write_text(exported, encoding="utf-8")
            results = repository.results(created["run_id"], 0, 100)["items"]
            assert len(results) == len(BenchmarkRegistry("router-core").cases)
            assert all(row["execution_status"] == "PASS" for row in results), [
                (row["case_id"], row["result"])
                for row in results
                if row["execution_status"] != "PASS"
            ]
            assert len(json.loads(exported)["results"]) == len(results)
            assert not runner.environment.active
        finally:
            await runner.close()

    try:
        asyncio.run(run())
    finally:
        engine.dispose()

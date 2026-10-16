"""公开执行条件与实际模型记录，使用原服务的隔离合成 Provider。"""

import asyncio
import json

from app.services.workflows.metadata import model_metadata
from workflow_fixture import host, graph, start, settle


def test_workflow_snapshot_excludes_configuration_secrets_and_observes_model_switch(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            config = app.state.llm_service.config
            config.api_key = "isolated-test-key"
            config.base_url = "https://private-endpoint.invalid/v1"
            definition = graph("llm_agent", {"instruction": "Return the fixture"})
            run_id = start(app, owner, definition)
            created = app.state.workflows.repository.run(run_id, owner)
            before = created["execution_snapshot"]
            assert before["models"]["planning_model"]["model"] == "workflow-fixture"
            config.model = "switched-fixture"
            result = await settle(app, owner, run_id)
            assert result["state"] == "COMPLETED"
            assert result["execution_snapshot"] == before
            step = next(node for node in result["nodes"] if node["node_id"] == "step")
            assert step["observed_model"]["model"] == "switched-fixture"
            assert all(node["scheduling_ms"] >= 0 for node in result["nodes"])
            payload = json.dumps(before) + json.dumps(model_metadata(app.state.llm_service))
            assert config.api_key not in payload and config.base_url not in payload
            assert "api_key" not in payload and "has_key" not in payload
            assert before["budget"] == definition.budget.model_dump(mode="json")
            assert before["dataset_version"] is None

    asyncio.run(run())

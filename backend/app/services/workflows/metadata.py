"""运行条件仅保留可比较的公开字段；不使用配置的完整序列化结果。"""

import platform

from app.services.recovery.checkpoints import digest
from app.services.desktop.preferences import read_preferences
from app.services.workflows.contracts import bounded


def model_metadata(service):
    config = getattr(service, "config", None)
    return bounded(
        {
            "provider": getattr(config, "provider", "unknown"),
            "model": getattr(config, "model", "unknown"),
            "temperature": getattr(config, "temperature", None),
            "max_tokens": getattr(config, "max_tokens", None),
            "timeout_seconds": getattr(config, "timeout_seconds", None),
        },
        limit=4000,
    )


def execution_snapshot(app, definition, identity):
    roles = {
        node.config.get("model_role", "planning_model")
        for node in definition.nodes
        if node.type in {"llm_agent", "agent_skill"}
    }
    return {
        "app_version": identity[0],
        "git_commit": identity[1],
        "prompt_version": identity[2],
        "schema_version": definition.schema_version,
        "definition_sha256": digest(definition.model_dump(mode="json")),
        "models": {role: model_metadata(app.state.model_roles.service(role)) for role in roles},
        "dependencies": {
            node.node_id: {
                "type": node.type,
                "tool_schema_hash": node.metadata.get("tool_schema_hash"),
                "schema_fingerprint": node.config.get("schema_fingerprint"),
                "content_hash": node.config.get("content_hash"),
            }
            for node in definition.nodes
            if node.type in {"tool", "mcp_tool", "agent_skill"}
        },
        "budget": definition.budget.model_dump(mode="json"),
        "permission_mode": read_preferences()["permission_mode"],
        "required_capabilities": definition.required_capabilities,
        "environment": {"os": platform.system(), "python": platform.python_version()},
        "dataset_version": None,  # 普通用户运行不是 Benchmark，不虚构数据集版本。
    }

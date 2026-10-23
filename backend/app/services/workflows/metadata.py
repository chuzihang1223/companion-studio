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


def workflow_data_kind(definition):
    # Tool/file input may enter any later node through references or transforms.
    # Classify the pinned graph conservatively before selecting its model snapshot.
    return (
        "ATTACHMENT"
        if any(
            node.type in {"tool", "mcp_tool", "knowledge_search", "computer_use"}
            or node.type == "input"
            and node.config.get("type") == "file_reference"
            for node in definition.nodes
        )
        else "TEXT"
    )


def workflow_service(routes, role, *, schema=False, current=False, data_kind="TEXT"):
    from app.services.llm.routing.contracts import TaskCapabilityResolver

    choose = getattr(routes, "preview", routes.service) if current else routes.service
    if getattr(routes, "intelligent", None):
        return choose(
            role,
            requirements=TaskCapabilityResolver.resolve(
                "STRUCTURED" if schema else "WORKFLOW", data_kind=data_kind
            ),
        )
    return choose(role)


def execution_snapshot(app, definition, identity, *, routes=None):
    roles = {
        node.config.get("model_role", "planning_model")
        for node in definition.nodes
        if node.type in {"llm_agent", "agent_skill"}
    }
    current_router = routes or app.state.model_roles
    if routes and getattr(routes, "intelligent", None) and routes.intelligent.policy.enabled:
        from app.services.llm.routing.contracts import TaskCapabilityResolver

        for node in definition.nodes:
            if node.type in {"llm_agent", "agent_skill"}:
                for task in ("WORKFLOW", "SCHEDULED"):
                    requirement = TaskCapabilityResolver.resolve(
                        "STRUCTURED" if node.config.get("output_schema") else task,
                        data_kind=workflow_data_kind(definition),
                    )
                    routes.service(
                        node.config.get("model_role", "planning_model"), requirements=requirement
                    )
    routing_identity = current_router.identity() if hasattr(current_router, "identity") else None
    return {
        **({"routing_identity": routing_identity} if routing_identity else {}),
        **({"model_routes": routes.selection_snapshot()} if routes and routing_identity else {}),
        "app_version": identity[0],
        "git_commit": identity[1],
        "prompt_version": identity[2],
        "schema_version": definition.schema_version,
        "definition_sha256": digest(definition.model_dump(mode="json")),
        "models": {
            role: model_metadata(
                workflow_service(
                    current_router,
                    role,
                    schema=any(
                        node.config.get("output_schema")
                        for node in definition.nodes
                        if node.config.get("model_role", "planning_model") == role
                    ),
                    current=routes is None,
                    data_kind=workflow_data_kind(definition),
                )
            )
            for role in roles
        },
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

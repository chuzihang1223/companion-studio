"""Resolve registered capabilities, never trust a graph's risk or executable names."""

from app.services.agent_tools import ToolRegistry
from app.services.recovery.checkpoints import digest
from app.services.workflows.contracts import WorkflowError
from app.services.workflows.variables import references
from app.services.knowledge.workflow import tool_node


class WorkflowCatalog:
    def __init__(self, app):
        self.app = app

    def public(self):
        registry = ToolRegistry()
        return {
            "tools": [
                {
                    "name": t.name,
                    "schema": t.schema()["function"]["parameters"],
                    "description": t.description,
                    "schema_fingerprint": digest(t.schema()),
                }
                for t in registry.by_api_name.values()
                if t.name != "skill.read_reference"
            ],
            "mcp_tools": self.app.state.external_tools.listing()
            if self.app.state.external_tools
            else [],
            "skills": self.app.state.agent_skills.listing() if self.app.state.agent_skills else [],
        }

    def validate_definition(self, definition):
        from app.services.skills.manager import CAPABILITIES

        names = {t.name for t in ToolRegistry().by_api_name.values()}
        if self.app.state.external_tools:
            names |= self.app.state.external_tools.available_names()
        for capability in definition.required_capabilities:
            if capability not in names and not (CAPABILITIES.get(capability, set()) & names):
                raise WorkflowError("WORKFLOW_CAPABILITY_UNAVAILABLE")

    def validate_node(self, node):
        node = tool_node(node)
        config = node.config
        if node.type == "tool":
            registry = ToolRegistry()
            tool = registry.by_api_name.get(str(config.get("name", "")).replace(".", "_"))
            if not tool or tool.name == "skill.read_reference":
                raise WorkflowError("WORKFLOW_TOOL_NOT_FOUND", node.node_id)
            if node.metadata.get("tool_schema_hash") not in {None, digest(tool.schema())}:
                raise WorkflowError("WORKFLOW_TOOL_SCHEMA_CHANGED", node.node_id)
            if not isinstance(config.get("arguments"), dict):
                raise WorkflowError("WORKFLOW_ARGUMENT_INVALID", node.node_id)
            if not list(references(config["arguments"])):
                self.parse_call(node, config["arguments"], "validation")
                if (
                    str(config.get("name", "")).startswith(("workspace.", "filesystem.", "code."))
                    and "path" in config["arguments"]
                ):
                    from app.core.config import settings
                    from app.services.workspace_paths import scoped_path

                    scoped_path(
                        settings.data_root / "agent_workspace" / "workflow-path-validation",
                        config["arguments"]["path"],
                    )
        if node.type == "mcp_tool":
            registry = self.app.state.external_tools
            if not registry:
                raise WorkflowError("MCP_SERVER_UNAVAILABLE", node.node_id)
            row = registry.get(config["tool_id"], config["schema_fingerprint"])
            if row["server_id"] != config["server_id"]:
                raise WorkflowError("MCP_SCHEMA_CHANGED", node.node_id)
        if node.type == "agent_skill":
            manager = self.app.state.agent_skills
            if not manager:
                raise WorkflowError("SKILL_PERMISSION_DENIED", node.node_id)
            manager.validate_selection(
                [{"skill_id": config["skill_id"], "content_hash": config["content_hash"]}]
            )
            row = manager.detail(config["skill_id"])
            names = {t.name for t in ToolRegistry().by_api_name.values()}
            if self.app.state.external_tools:
                names |= self.app.state.external_tools.available_names()
            if manager.dependencies(row, names):
                raise WorkflowError("SKILL_DEPENDENCY_MISSING", node.node_id)

    def pin(self, definition):
        result = definition.model_copy(deep=True)
        registry = ToolRegistry()
        for node in result.nodes:
            if node.type in {"tool", "knowledge_search", "computer_use"}:
                normalized = tool_node(node)
                tool = registry.by_api_name.get(
                    str(normalized.config.get("name", "")).replace(".", "_")
                )
                if tool:
                    node.metadata["tool_schema_hash"] = digest(tool.schema())
        return result

    def parse_call(self, node, arguments, call_id):
        node = tool_node(node)
        import json
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError

        registry = ToolRegistry(self.app.state.external_tools)
        name = node.config.get("name") if node.type == "tool" else node.config["tool_id"]
        if node.type == "tool":
            tool = registry.by_api_name.get(name.replace(".", "_"))
            if not tool:
                raise WorkflowError("WORKFLOW_TOOL_NOT_FOUND", node.node_id)
            try:
                Draft202012Validator(tool.schema()["function"]["parameters"]).validate(arguments)
            except ValidationError:
                raise WorkflowError("WORKFLOW_ARGUMENT_INVALID", node.node_id) from None
        call = registry.parse_calls(
            {
                "tool_calls": [
                    {
                        "id": call_id,
                        "function": {
                            "name": name.replace(".", "_"),
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ]
            }
        )[0]
        if node.type == "mcp_tool" and (
            call["schema_fingerprint"] != node.config["schema_fingerprint"]
            or call["server_id"] != node.config["server_id"]
        ):
            raise WorkflowError("MCP_SCHEMA_CHANGED", node.node_id)
        return call

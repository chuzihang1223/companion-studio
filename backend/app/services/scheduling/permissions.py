"""An extra conservative admission check; never replaces or grants original tool permissions."""

from app.services.recovery.checkpoints import digest
from app.services.scheduling.conditions import scope_identity
from app.services.scheduling.contracts import ScheduleError
from app.services.scheduling.time_rules import utc
from app.services.workflows.metadata import execution_snapshot, workflow_service, workflow_data_kind
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

PURE = {"manual_trigger", "input", "condition", "merge", "transform", "output"}
READ_ONLY = {
    "workspace.read",
    "workspace.list",
    "workspace.search",
    "code.read",
    "knowledge.search",
    "knowledge.collections",
    "knowledge.get_document",
    "knowledge.get_chunk",
}


class ScheduledPermissionAdapter:
    def __init__(self, app):
        self.app = app

    def snapshot(self, definition):
        workflows = self.app.state.workflows
        owner = definition.conversation_id
        workspace = workflows.scope(owner)
        draft = workflows.repository.get(definition.workflow_id)
        if draft["definition"]["status"] == "ARCHIVED":
            raise ScheduleError("SCHEDULE_WORKFLOW_UNAVAILABLE")
        graph = workflows.repository.version(definition.workflow_version_id)
        if graph.workflow_id != definition.workflow_id or graph.status != "PUBLISHED":
            raise ScheduleError("SCHEDULE_PUBLISHED_VERSION_REQUIRED")
        workflows.validator.compile(graph)
        try:
            Draft202012Validator(graph.input_schema).validate(definition.inputs)
        except ValidationError:
            raise ScheduleError("SCHEDULE_INPUT_INVALID") from None
        snapshot = execution_snapshot(self.app, graph, workflows.build_identity)
        # The URL itself is private configuration, only its digest is persisted.
        model_targets = {}
        for role in snapshot["models"]:
            routes = self.app.state.model_roles
            config = getattr(
                workflow_service(
                    routes,
                    role,
                    schema=any(
                        node.config.get("output_schema")
                        for node in graph.nodes
                        if node.config.get("model_role", "planning_model") == role
                    ),
                    current=True,
                    data_kind=workflow_data_kind(graph),
                ),
                "config",
                None,
            )
            model_targets[role] = digest(
                {
                    "provider": getattr(config, "provider", None),
                    "model": getattr(config, "model", None),
                    "endpoint": getattr(config, "base_url", None),
                }
            )
        dependencies = {
            "models": snapshot["models"],
            **(
                {"routing_identity": snapshot["routing_identity"]}
                if snapshot.get("routing_identity")
                else {}
            ),
            "model_targets": model_targets,
            "tools": snapshot["dependencies"],
            "capabilities": graph.required_capabilities,
        }
        return {
            "schedule_id": definition.schedule_id,
            "owner_id": owner,
            "definition_hash": digest(graph.model_dump(mode="json")),
            "dependencies_hash": digest(dependencies),
            "inputs_hash": digest(definition.inputs),
            "workspace_hash": digest(scope_identity(workspace)),
            "workflow_version_id": definition.workflow_version_id,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "type": node.type,
                    "tool": node.config.get("name"),
                    "requires_interaction": not self.safe(node),
                }
                for node in graph.nodes
            ],
            "required_capabilities": graph.required_capabilities,
            "may_use_models": bool(snapshot["models"]),
            "grants_permissions": False,
        }

    @staticmethod
    def safe(node):
        if node.type == "knowledge_search":
            return node.config.get("mode", "keyword") == "keyword"
        if node.type == "tool" and node.config.get("name") == "knowledge.search":
            arguments = node.config.get("arguments", {})
            # Whole-object references hide the eventual embedding mode. Only a
            # fixed keyword mode may be admitted unattended; consent cannot turn
            # this into a remote embedding grant.
            return "$ref" not in arguments and arguments.get("mode", "keyword") == "keyword"
        if node.type in PURE:
            return not (
                node.type == "input" and node.config.get("type") == "file_reference"
            ) and not (
                node.type == "output"
                and node.config.get("format") in {"file_reference", "evaluation_reference"}
            )
        return (
            node.type == "knowledge_search"
            or node.type == "tool"
            and node.config.get("name") in READ_ONLY
        )

    def inspect(self, definition, expected, permission, now, node=None):
        snapshot = self.snapshot(definition)
        if snapshot != expected:
            raise ScheduleError("SCHEDULE_DEPENDENCY_CHANGED")
        graph = self.app.state.workflows.repository.version(definition.workflow_version_id)
        selected = [node] if node else graph.nodes
        for item in selected:
            if (
                item.type == "knowledge_search"
                or item.type == "tool"
                and item.config.get("name") == "knowledge.search"
            ) and not self.safe(item):
                raise ScheduleError("SCHEDULE_KEYWORD_SEARCH_REQUIRED")
        needs = any(not self.safe(item) for item in selected)
        valid = (
            permission
            and utc(permission["expires_at"]) > now
            and permission["binding"] == digest(expected)
        )
        return {
            "allowed": not needs or bool(valid),
            "requires_permission": needs,
            "safe_unattended": not needs,
            "snapshot": snapshot,
        }

"""Node-specific configuration checks before any execution or permission prompt."""

from app.services.workflows.contracts import WorkflowError


def validate_config(node):
    c = node.config

    def fail():
        raise WorkflowError("WORKFLOW_CONFIG_INVALID", node.node_id)

    if node.type in {"llm_agent", "agent_skill"}:
        instruction = c.get("instruction" if node.type == "llm_agent" else "goal")
        if not isinstance(instruction, str) or not 1 <= len(instruction) <= 8000:
            fail()
        if "max_characters" in c and (
            type(c["max_characters"]) is not int or not 1 <= c["max_characters"] <= 32000
        ):
            fail()
        if c.get("model_role", "planning_model") not in {
            "planning_model",
            "character_model",
            "tool_execution_model",
            "coding_model",
        }:
            fail()
    if node.type in {"tool", "mcp_tool"} and not isinstance(c.get("arguments"), dict):
        fail()
    if node.type == "tool" and c.get("coding_mode", "virtual_only") not in {
        "virtual_only",
        "preview_patch",
        "write_workspace",
    }:
        fail()
    if node.type == "human_approval" and (
        not isinstance(c.get("message"), str) or not 1 <= len(c["message"]) <= 2000
    ):
        fail()
    if node.type == "transform" and c.get("operation") not in {
        "select",
        "concat",
        "default",
        "number",
        "length",
        "map",
        "extract",
    }:
        fail()
    if node.type == "output" and c.get("format", "json") not in {
        "json",
        "text",
        "file_reference",
        "task_summary",
        "trace_reference",
        "evaluation_reference",
    }:
        fail()

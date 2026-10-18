"""Seven bounded tools; the same validation is used for model, UI and workflow."""

from app.services.computer_use.contracts import Action, ExplicitAction

OPERATIONS = (
    "observe",
    "find_element",
    "click",
    "type_text",
    "scroll",
    "focus_window",
    "wait_for_state",
)
READS = {"observe", "find_element", "wait_for_state"}


def validate(operation, arguments):
    value = ExplicitAction.model_validate(
        {
            "conversation_id": "00000000-0000-4000-8000-000000000001",
            "character_id": "validation",
            "operation": operation,
            "arguments": arguments,
        }
    )
    return value.arguments.model_dump(mode="json", exclude_none=True)


def definitions():
    schema = Action.model_json_schema()

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return inline(schema["$defs"][value["$ref"].rsplit("/", 1)[-1]])
            return {
                key: inline(item) for key, item in value.items() if key not in {"default", "title"}
            }
        return value

    for operation in OPERATIONS:
        yield {
            "name": "computer." + operation,
            "description": "Operate only a window explicitly selected in Visual Observer for this conversation. "
            "No raw coordinates, executable paths, scripts or screenshots. "
            "Find returns opaque fresh candidate IDs. Ambiguous/stale/unknown effects require reobservation, never repeat input. "
            + operation,
            "properties": inline(schema["properties"]),
            "required": ["target_id"],
            "risk_level": "low"
            if operation in READS
            else "high"
            if operation == "click"
            else "medium",
            "requires_confirmation": True,
        }

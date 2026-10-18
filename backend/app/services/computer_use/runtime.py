"""Safe metadata only crosses DesktopBridge into persisted plans and model context."""

from app.services.computer_use.tools import validate, READS
from app.services.trace.scope import current_trace, span_scope


def event(name, result=None):
    trace = current_trace.get()
    if trace:
        result = result or {}
        trace.send(
            "event",
            event_type=name,
            phase="computer",
            metadata={
                key: result[key]
                for key in (
                    "window_id",
                    "observation_id",
                    "action_id",
                    "duration_ms",
                    "verified",
                    "source",
                )
                if key in result
            },
        )


async def execute(app, context, call):
    operation = call["name"].split(".", 1)[1]
    args = validate(operation, call["arguments"])
    # Retrieved source instructions cannot turn private knowledge into an input
    # for another application. Screen text itself never enters model context.
    if context.knowledge_tainted and operation not in READS:
        return {"verified": False, "code": "PERMISSION_DENIED", "status": "NEEDS_REVIEW"}
    from app.services.desktop.preferences import read_preferences

    if not read_preferences()["automation_enabled"]:
        return {"verified": False, "code": "PERMISSION_DENIED"}
    with span_scope(
        "operation",
        "COMPUTER_ACTION_STARTED",
        "COMPUTER_ACTION_COMPLETED",
        phase="computer",
        tool_name=call["name"],
    ):
        result = await app.state.desktop_bridge.request(
            call["name"],
            {
                **args,
                "owner": {
                    "conversation_id": context.conversation_id,
                    "character_id": context.character_id,
                },
            },
            context.work_session_id,
            timeout=30,
        )
        allowed = {
            "target_id",
            "window_id",
            "observation_id",
            "action_id",
            "status",
            "verified",
            "executed",
            "code",
            "source",
            "candidate_count",
            "candidates",
            "element_count",
            "ocr_count",
            "duration_ms",
            "capture_available",
            "capture_error",
            "ocr_error",
            "evidence_refs",
        }
        result = {key: value for key, value in result.items() if key in allowed}
        if operation in {"observe", "find_element"}:
            event("SCREEN_OBSERVED", result)
            event("UIA_TREE_CAPTURED", result)
            if result.get("ocr_count"):
                event("OCR_COMPLETED", result)
            if operation == "find_element":
                event("ELEMENT_LOCATED", result)
        if result.get("verified") is False:
            event("ACTION_VERIFICATION_FAILED", result)
        return result

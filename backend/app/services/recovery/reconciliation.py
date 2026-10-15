"""Fresh, bounded observations; a historical success flag is never current proof."""

import hashlib
import os

from app.services.recovery.checkpoints import RecoveryError, decoded, plan_hash, workspace_hash
from app.services.recovery.policy import FILE_READS, FILE_WRITES, READ_ONLY


def file_hash(target):
    if not target.exists():
        return "ABSENT"
    if not target.is_file() or target.stat().st_size > 1_000_000:
        return "UNAVAILABLE"
    return hashlib.sha256(target.read_bytes()).hexdigest()


def resource_snapshot(context, call):
    """Read only the exact scoped file/draft. No project walk, no raw file bodies."""
    name, args = call["name"], call["arguments"]
    if name not in FILE_READS | FILE_WRITES:
        return {"kind": "external" if name not in READ_ONLY else "read_only"}
    path = args.get("path")
    target = context.executor._path(path)  # rejects traversal/private files/symlinks outside root
    virtual = name in FILE_WRITES and context.coding_mode != "write_workspace"
    draft = context.executor._draft(path) if hasattr(context.executor, "_draft") else None
    current = (
        hashlib.sha256(draft["content"].encode("utf-8")).hexdigest()
        if draft and (virtual or name in FILE_READS)
        else "ABSENT"
        if virtual
        else file_hash(target)
    )
    result = {"kind": "virtual_file" if virtual else "file", "path": path, "before_hash": current}
    if name in FILE_WRITES:
        content = args.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > 100_000:
            raise RecoveryError("TOOL_ARGUMENT_INVALID")
        # Existing physical write_text uses platform newline translation. Hash the
        # actual expected bytes rather than pretending LF survives Windows writes.
        output = content if virtual else content.replace("\n", os.linesep)
        result["expected_hash"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
    return result


class StateReconciler:
    def __init__(self, app, manager):
        self.app, self.manager = app, manager

    async def inspect(self, root, context):
        import asyncio

        detail = self.manager.detail(root, context.conversation_id, context.work_session_id)
        with self.manager.factory() as db:
            row = self.manager.scope(db, root, context.conversation_id, context.work_session_id)
            if row.plan_hash != plan_hash(context) or row.workspace_hash != workspace_hash(
                context, self.app.state.work_state
            ):
                raise RecoveryError("CHECKPOINT_CONFLICT")
        by_call = {s["tool_call_id"]: s for s in detail["checkpoints"]}
        items = []
        for index, call in enumerate(context.calls):
            step = by_call.get(call["id"])
            decision, code = "EXECUTE", None
            snapshot = await asyncio.to_thread(resource_snapshot, context, call)
            if step:
                if step["tool_name"] != call["name"] or step["step_index"] != index:
                    raise RecoveryError("CHECKPOINT_CONFLICT")
                intent = step["execution_intent"]
                if call["name"] in FILE_READS | FILE_WRITES:
                    current = snapshot.get("before_hash")
                    wanted = (
                        intent.get("expected_hash")
                        if call["name"] in FILE_WRITES
                        else intent.get("before_hash")
                    )
                    if current == "UNAVAILABLE":
                        decision, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
                    elif (
                        wanted
                        and current == wanted
                        and (
                            step["step_state"] == "COMMITTED"
                            or call["name"] in FILE_WRITES
                            and step["step_state"] not in {"PREPARED", "DENIED"}
                        )
                    ):
                        decision = "SKIP_VERIFIED"
                    elif step["step_state"] in {"PREPARED", "DENIED"} and current == intent.get(
                        "before_hash"
                    ):
                        decision = "EXECUTE"
                    elif call["name"] in FILE_READS and step["step_state"] != "COMMITTED":
                        decision = "EXECUTE"
                    else:
                        decision, code = "REVIEW", "FILE_STATE_CHANGED"
                elif call["name"] in READ_ONLY:
                    # Fresh observations are safe; committed reads are checked by a
                    # read-only probe below, never replayed as side-effectful calls.
                    if step["step_state"] == "COMMITTED":
                        if call["name"] in {
                            "app.detect",
                            "app.status",
                            "system.get_status",
                            "media.current",
                            "media.sessions",
                            "browser.observe",
                        }:
                            op = (
                                "system.status"
                                if call["name"] == "system.get_status"
                                else call["name"]
                            )
                            observed = await self.app.state.desktop_bridge.request(
                                op, call["arguments"], context.work_session_id, timeout=8
                            )
                            decision = (
                                "SKIP_VERIFIED" if observed.get("verified") is True else "REVIEW"
                            )
                        else:
                            decision, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
                elif step["step_state"] not in {"PREPARED", "DENIED"}:
                    # No stale window handles, script exit flags or form receipts
                    # are treated as proof. Unknown external effects require review.
                    decision, code = "REVIEW", "SIDE_EFFECT_UNCERTAIN"
            items.append(
                {
                    "step_index": index,
                    "tool_call_id": call["id"],
                    "tool_name": call["name"],
                    "action": decision,
                    "error_code": code,
                    "checkpoint_id": step["checkpoint_id"] if step else None,
                    "side_effect_class": step["side_effect_class"] if step else snapshot["kind"],
                    "observation": {
                        "resource_available": snapshot.get("before_hash") != "UNAVAILABLE",
                        "matches_expected": decision == "SKIP_VERIFIED",
                    },
                    "requires_approval": decision == "EXECUTE" and call["name"] not in READ_ONLY,
                }
            )
        await asyncio.to_thread(
            self.manager.record_checks,
            root,
            sum(bool(item["checkpoint_id"]) for item in items),
            sum(bool(item["checkpoint_id"]) and item["action"] != "REVIEW" for item in items),
        )
        return {
            "task_id": root,
            "conversation_id": context.conversation_id,
            "work_session_id": context.work_session_id,
            "revision": detail["revision"],
            "items": items,
            "can_resume": not any(s["action"] == "REVIEW" for s in items),
            "extra_model_requests": 0,
            "external_exactly_once": False,
            "summary": "先重新核验，再跳过已有结果；未知副作用不自动重复。",
        }

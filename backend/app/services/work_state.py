"""Persistent Workbench progress plus event notifications."""

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import select, delete

from app.database.session import SessionLocal
from app.core.file_logging import file_logger
from app.models import (
    Conversation,
    WorkEvent,
    WorkPermission,
    WorkSession,
    WorkflowStep,
    WorkspaceTab,
)


class WorkState:
    def __init__(self):
        self.subscribers: dict[str, set[asyncio.Queue[int]]] = {}
        self.logger = file_logger("app.work", "work.log")
        self.loop = None
        self.trace_recorder = None
        self.trace_revisions = OrderedDict()
        self.recovery_revisions = OrderedDict()

    def notify_recovery(self, session_id: str) -> None:
        # Same bounded SSE channel, independent invalidation sentinel. A ledger
        # update is not execution progress and must not reset the watchdog.
        self.recovery_revisions[session_id] = self.recovery_revisions.get(session_id, 0) + 1
        self.recovery_revisions.move_to_end(session_id)
        while len(self.recovery_revisions) > 1024:
            self.recovery_revisions.popitem(last=False)

        def publish():
            for queue in list(self.subscribers.get(session_id, set())):
                try:
                    queue.put_nowait(-1)
                except asyncio.QueueFull:
                    pass

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(publish)

    def notify_trace(self, session_id: str) -> None:
        # Projection invalidation shares SSE subscriptions, but is not task progress:
        # do not persist a second WorkEvent or feed the registry watchdog.
        self.trace_revisions[session_id] = self.trace_revisions.get(session_id, 0) + 1
        self.trace_revisions.move_to_end(session_id)
        while len(self.trace_revisions) > 1024:
            self.trace_revisions.popitem(last=False)

        def publish():
            for queue in list(self.subscribers.get(session_id, set())):
                try:
                    queue.put_nowait(0)
                except asyncio.QueueFull:
                    pass

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(publish)

    def ensure(self, conversation_id: str) -> str:
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            if (
                not conversation
                or conversation.is_deleted
                or conversation.conversation_type == "chat"
            ):
                raise LookupError("请选择工作会话")
            row = db.scalar(
                select(WorkSession)
                .where(WorkSession.conversation_id == conversation_id)
                .order_by(WorkSession.created_at.desc())
            )
            if row:
                return row.id
        session = self.create("尚未开始任务", conversation_id, [])
        self.set_steps(session, [])
        self.emit(session, "workflow_updated", {}, status="idle")
        return session

    def set_workspace(self, session_id: str, root: str | None) -> None:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            if not row:
                raise LookupError("工作会话不存在")
            if row.status not in {"idle", "done", "completed", "failed", "stopped", "cancelled"}:
                raise ValueError("请先停止当前任务，再更换工作文件夹并重新加载上下文")
            row.workspace_root = root
            plan = json.loads(row.plan_json or "{}")
            plan["workspace_root"] = root
            row.plan_json = json.dumps(plan, ensure_ascii=False)
            if root is None:
                db.execute(
                    delete(WorkspaceTab).where(
                        WorkspaceTab.session_id == session_id, WorkspaceTab.kind == "files"
                    )
                )
            db.commit()
        if root:
            self.module(session_id, "files", "项目文件", {"root": root})
        self.emit(session_id, "workspace_changed", {"root": root})

    def create(
        self,
        goal: str,
        conversation_id: str | None,
        calls: list[dict],
        workspace_root: str | None = None,
    ) -> str:
        session_id = str(uuid4())
        with SessionLocal() as db:
            previous = (
                db.scalar(
                    select(WorkSession)
                    .where(WorkSession.conversation_id == conversation_id)
                    .order_by(WorkSession.created_at.desc())
                )
                if conversation_id
                else None
            )
            prior_tabs = (
                list(db.scalars(select(WorkspaceTab).where(WorkspaceTab.session_id == previous.id)))
                if previous and previous.workspace_root == workspace_root
                else []
            )
            db.add(
                WorkSession(
                    id=session_id,
                    conversation_id=conversation_id,
                    goal=goal[:4000],
                    workspace_root=workspace_root,
                    status="waiting_permission"
                    if any(call["requires_confirmation"] for call in calls)
                    else "planning",
                )
            )
            # Copy views for this conversation and unchanged root; preserve original task records.
            for tab in prior_tabs:
                db.add(
                    WorkspaceTab(
                        id=str(uuid4()),
                        session_id=session_id,
                        kind=tab.kind,
                        title=tab.title,
                        data_json=tab.data_json,
                    )
                )
            for position, title in enumerate(("理解目标", "执行工具", "整理结果", "完成")):
                db.add(
                    WorkflowStep(
                        session_id=session_id,
                        position=position,
                        title=title,
                        status="done" if position == 0 else "pending",
                    )
                )
            for call in calls:
                if call["requires_confirmation"]:
                    db.add(
                        WorkPermission(
                            session_id=session_id, call_id=call["id"], tool_name=call["name"]
                        )
                    )
            db.commit()
        if conversation_id:
            self.link_conversation(session_id, conversation_id)
        self.emit(
            session_id,
            "workflow_updated",
            {
                "status": "waiting_permission"
                if any(call["requires_confirmation"] for call in calls)
                else "planning"
            },
        )
        requested = [call["name"] for call in calls if call["requires_confirmation"]]
        if requested:
            self.emit(
                session_id, "permission_requested", {"count": len(requested), "tools": requested}
            )
        return session_id

    def recover_interrupted(self) -> None:
        """A crashed process cannot continue its old tool execution automatically."""
        with SessionLocal() as db:
            ids = list(
                db.scalars(
                    select(WorkSession.id).where(
                        WorkSession.status.in_(
                            [
                                "queued",
                                "running",
                                "summarizing",
                                "executing",
                                "observing",
                                "verifying",
                                "retrying",
                                "paused",
                                "waiting_user",
                            ]
                        )
                    )
                )
            )
        for session_id in ids:
            self.emit(
                session_id,
                "work_interrupted",
                {"detail": "程序已重启；上次正在执行的任务已停止，请检查结果后重新发起。"},
                status="stopped",
            )

    def emit(
        self,
        session_id: str,
        kind: str,
        detail: dict,
        *,
        status: str | None = None,
        step: int | None = None,
    ) -> None:
        with SessionLocal() as db:
            session = db.get(WorkSession, session_id)
            if session is None:
                return
            if status:
                session.status = status
                session.updated_at = datetime.now(timezone.utc)
            if session.conversation_id:
                conversation = db.get(Conversation, session.conversation_id)
                if conversation:
                    conversation.updated_at = datetime.now(timezone.utc)
            if step is not None:
                rows = db.scalars(
                    select(WorkflowStep).where(WorkflowStep.session_id == session_id)
                ).all()
                for row in rows:
                    row.status = (
                        "done"
                        if row.position < step
                        else "active"
                        if row.position == step
                        else "pending"
                    )
            registry = getattr(self, "registry", None)
            detail = {
                **detail,
                **(registry.envelope(session_id) if registry else {"task_id": session_id}),
                "conversation_id": session.conversation_id,
                "work_session_id": session_id,
                "event_type": kind,
            }
            event = WorkEvent(
                session_id=session_id, type=kind, detail_json=json.dumps(detail, ensure_ascii=False)
            )
            db.add(event)
            db.commit()
            event_id = event.id
        self.logger.info("session=%s event=%s status=%s", session_id, kind, status or "unchanged")
        if registry:
            registry.event(session_id, kind, status)
        if self.trace_recorder:
            try:
                self.trace_recorder.project_work_event(
                    session_id, detail.get("conversation_id"), kind, detail, event_id
                )
            except Exception:
                pass  # Optional projection must never roll back the business event.

        def publish():
            for queue in list(self.subscribers.get(session_id, set())):
                try:
                    queue.put_nowait(event_id)
                except asyncio.QueueFull:
                    pass

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(publish)

    def decide(self, session_id: str, approved_ids: set[str]) -> None:
        with SessionLocal() as db:
            for row in db.scalars(
                select(WorkPermission).where(WorkPermission.session_id == session_id)
            ):
                row.decision = "allow_once" if row.call_id in approved_ids else "denied"
            db.commit()
        self.emit(
            session_id,
            "permission_resolved",
            {"approved_count": len(approved_ids)},
            status="running",
            step=1,
        )

    def add_tab(self, session_id: str, kind: str, title: str, data: dict) -> str:
        if kind not in {
            "web",
            "code",
            "diff",
            "terminal",
            "system",
            "app",
            "file",
            "files",
            "media",
            "preview",
            "artifacts",
        }:
            raise ValueError("Unsupported workspace tab")
        serialized = json.dumps(data, ensure_ascii=False)
        if len(serialized) > 32_000:
            raise ValueError("工作区内容超过 32 KB")
        tab_id = str(uuid4())
        with SessionLocal() as db:
            if db.get(WorkSession, session_id) is None:
                raise LookupError("Work session not found")
            db.add(
                WorkspaceTab(
                    id=tab_id,
                    session_id=session_id,
                    kind=kind,
                    title=title[:100],
                    data_json=serialized,
                )
            )
            db.commit()
        self.emit(
            session_id, "workspace_opened", {"id": tab_id, "kind": kind, "title": title[:100]}
        )
        if (
            kind == "app"
            and data.get("action") == "launch"
            and data.get("status") == "launch_requested"
        ):
            self.emit(session_id, "app_launch_requested", {"app": str(data.get("app", ""))[:50]})
        elif kind == "app" and data.get("action") == "launch" and data.get("status") == "running":
            self.emit(
                session_id,
                "app_launched",
                {"app": str(data.get("app", ""))[:50], "status": "running"},
            )
        return tab_id

    def set_steps(self, session_id: str, titles: list[str]) -> None:
        with SessionLocal() as db:
            db.execute(delete(WorkflowStep).where(WorkflowStep.session_id == session_id))
            for position, title in enumerate(titles):
                db.add(
                    WorkflowStep(
                        session_id=session_id,
                        position=position,
                        title=title[:180],
                        status="pending",
                    )
                )
            db.commit()
        self.emit(session_id, "plan_updated", {"steps": titles})

    def step_status(self, session_id: str, position: int, status: str) -> None:
        with SessionLocal() as db:
            row = db.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.session_id == session_id, WorkflowStep.position == position
                )
            )
            if row:
                row.status = status
                db.commit()

    def module(self, session_id: str, kind: str, title: str, data: dict) -> str:
        with SessionLocal() as db:
            row = db.scalar(
                select(WorkspaceTab).where(
                    WorkspaceTab.session_id == session_id,
                    WorkspaceTab.kind == kind,
                    WorkspaceTab.title == title,
                )
            )
            tab_id = row.id if row else None
        if tab_id:
            self.update_tab(session_id, tab_id, data)
            return tab_id
        return self.add_tab(session_id, kind, title, data)

    def status(self, session_id: str) -> str:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            return row.status if row else "stopped"

    def save_plan(self, session_id: str, plan: dict) -> None:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            row.plan_json = json.dumps(plan, ensure_ascii=False)
            db.commit()

    def restore_plan(self, plan_id: str) -> dict | None:
        with SessionLocal() as db:
            rows = db.scalars(
                select(WorkSession).where(
                    WorkSession.status.in_(["planning", "waiting_permission"])
                )
            )
            for row in rows:
                plan = json.loads(row.plan_json or "{}")
                if plan.get("plan_id") == plan_id:
                    return plan
        return None

    def update_tab(self, session_id: str, tab_id: str, data: dict | None) -> None:
        with SessionLocal() as db:
            row = db.get(WorkspaceTab, tab_id)
            if not row or row.session_id != session_id:
                raise LookupError("Workspace tab not found")
            if data is None:
                db.delete(row)
            else:
                if len(json.dumps(data)) > 32000:
                    raise ValueError("工作区内容超过 32 KB")
                row.data_json = json.dumps(data, ensure_ascii=False)
                if isinstance(data.get("title"), str):
                    row.title = data["title"][:100]
            db.commit()
        self.emit(session_id, "workspace_updated", {"id": tab_id})

    def snapshot(self, session_id: str) -> dict:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            if row is None:
                raise LookupError("Work session not found")
            steps = db.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.session_id == session_id)
                .order_by(WorkflowStep.position)
            ).all()
            events = db.scalars(
                select(WorkEvent)
                .where(WorkEvent.session_id == session_id)
                .order_by(WorkEvent.id.desc())
                .limit(100)
            ).all()
            tabs = db.scalars(
                select(WorkspaceTab).where(WorkspaceTab.session_id == session_id)
            ).all()
            permissions = db.scalars(
                select(WorkPermission).where(WorkPermission.session_id == session_id)
            ).all()
            plan = json.loads(row.plan_json or "{}")
            return {
                "id": row.id,
                "trace_revision": self.trace_revisions.get(row.id, 0),
                "recovery_revision": self.recovery_revisions.get(row.id, 0),
                "conversation_id": row.conversation_id,
                "goal": row.goal,
                "status": row.status,
                "workspace_root": row.workspace_root,
                "pending_plan": {
                    "plan_id": plan["plan_id"],
                    "calls": plan["calls"],
                    "local_resolution": plan.get("local_resolution"),
                }
                if plan.get("plan_id") and row.status in {"planning", "waiting_permission"}
                else None,
                "tool_results": [
                    json.loads(event.detail_json)
                    for event in reversed(events)
                    if event.type == "tool_completed"
                ],
                "steps": [
                    {"id": step.id, "title": step.title, "status": step.status} for step in steps
                ],
                "events": [
                    {
                        "id": event.id,
                        "type": event.type,
                        "detail": json.loads(event.detail_json),
                        "created_at": event.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    }
                    for event in reversed(events)
                ],
                "tabs": [
                    {
                        "id": tab.id,
                        "kind": tab.kind,
                        "title": tab.title,
                        "data": json.loads(tab.data_json),
                    }
                    for tab in tabs
                ],
                "permissions": [
                    {
                        "call_id": item.call_id,
                        "tool_name": item.tool_name,
                        "decision": item.decision,
                    }
                    for item in permissions
                ],
            }

    def stop(self, session_id: str) -> None:
        self.emit(session_id, "work_stopped", {}, status="stopped")

    def link_conversation(self, session_id: str, conversation_id: str) -> None:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            if row:
                row.conversation_id = conversation_id
                conversation = db.get(Conversation, conversation_id)
                latest = db.scalar(
                    select(WorkSession.id)
                    .where(WorkSession.conversation_id == conversation_id)
                    .order_by(WorkSession.created_at.desc())
                )
                if conversation and latest == session_id:
                    conversation.work_session_id = session_id
                    conversation.updated_at = datetime.now(timezone.utc)
                db.commit()

    def for_conversation(self, conversation_id: str) -> dict:
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            if not conversation or conversation.is_deleted:
                raise LookupError("Conversation not found")
            row = db.scalar(
                select(WorkSession)
                .where(WorkSession.conversation_id == conversation_id)
                .order_by(WorkSession.created_at.desc())
            )
            if row is None:
                raise LookupError("No work session for conversation")
            session_id = row.id
        return self.snapshot(session_id)

    def is_stopped(self, session_id: str) -> bool:
        with SessionLocal() as db:
            row = db.get(WorkSession, session_id)
            return row is None or row.status in {"stopped", "cancelled"}

    async def stream(self, session_id: str, after: int = 0):
        self.loop = asyncio.get_running_loop()
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=100)
        self.subscribers.setdefault(session_id, set()).add(queue)
        try:
            last = after
            while True:
                with SessionLocal() as db:
                    rows = db.scalars(
                        select(WorkEvent)
                        .where(WorkEvent.session_id == session_id, WorkEvent.id > last)
                        .order_by(WorkEvent.id)
                        .limit(100)
                    ).all()
                    for item in rows:
                        last = item.id
                        yield f"id: {item.id}\nevent: {item.type}\ndata: {item.detail_json}\n\n"
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=15)
                    if notification == 0:
                        yield "event: trace_updated\ndata: {}\n\n"
                    elif notification == -1:
                        yield "event: recovery_updated\ndata: {}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.subscribers.get(session_id, set()).discard(queue)

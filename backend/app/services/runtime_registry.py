"""记录任务所有权并限制执行并发；记录不包含 Prompt 或密钥。

同类任务在同一会话内串行，不同会话受共享 Semaphore 限制。启动时
只标记遗留任务为中断，不恢复旧协程，也不自动重放已有工具操作。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from sqlalchemy import select
from app.database.session import SessionLocal
from app.models.runtime_task import RuntimeTask

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "stalled", "stopped"}
TaskKind = Literal["agent", "chat", "voice", "workflow"]


class RuntimeTaskRegistry:
    """协调一次执行的 Task ID、会话及工作会话，释放当前任务占用资源。"""

    def __init__(self) -> None:
        self.gates = {
            "workflow": asyncio.Semaphore(2),
            "agent": asyncio.Semaphore(3),
            "chat": asyncio.Semaphore(4),
            "voice": asyncio.Semaphore(2),
        }
        self.conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.active: dict[str, asyncio.Task[Any] | None] = {}
        self.by_session: dict[str, str] = {}
        self.progress: dict[str, tuple[float, str | None]] = {}
        self.trace_recorder = None
        with SessionLocal() as db:
            for row in db.scalars(select(RuntimeTask).where(RuntimeTask.status.not_in(TERMINAL))):
                row.status = "interrupted"
                row.wait_reason = "process_restarted"
            db.commit()

    def _trace(self, task_id: str, stage: str) -> None:
        if self.trace_recorder:
            try:
                self.trace_recorder.lifecycle(task_id, stage)
            except Exception:
                pass  # Observability never changes gate/lock/cancellation behavior.

    def update(self, task_id: str, status: str, reason: str | None = None) -> None:
        """记录单调时钟进度，并只更新尚未进入终态的持久记录。"""
        self.progress[task_id] = (time.monotonic(), reason)
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id)
            if row and row.status not in TERMINAL:
                row.status, row.wait_reason = status, reason
                row.updated_at = datetime.now(timezone.utc)
                db.commit()

    def event(self, session_id: str, kind: str, status: str | None = None) -> None:
        """把工作事件关联到当前活动任务；事件名称同时用作等待原因。"""
        task_id = self.by_session.get(session_id)
        if task_id:
            self.update(task_id, status or "executing", kind)

    def envelope(self, session_id: str) -> dict[str, str | None]:
        """提供事件所有权字段；不存在活动映射时以 Session ID 回退。"""
        task_id = self.by_session.get(session_id)
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id) if task_id else None
            return {
                "task_id": task_id or session_id,
                "renderer_id": row.renderer_id if row else None,
            }

    @asynccontextmanager
    async def run(
        self,
        task_id: str,
        conversation_id: str | None,
        session_id: str | None = None,
        renderer_id: str | None = None,
        kind: TaskKind = "agent",
    ) -> AsyncIterator[None]:
        """登记并执行一次任务，重复编号拒绝，退出时释放已获得的门与锁。

        获取会话锁和共享配额各有 120 秒等待限制，进入执行后为 1800 秒。
        锁键含 kind，因此不同类别的同会话任务不互斥；plan 阶段在此之外。
        """
        if task_id in self.active:
            raise ValueError("同一任务正在执行，请勿重复提交")
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id)
            if row:
                raise ValueError("任务已经执行过，请重新发送新任务")
            db.add(
                RuntimeTask(
                    id=task_id,
                    conversation_id=conversation_id,
                    work_session_id=session_id,
                    renderer_id=renderer_id,
                    kind=kind,
                    status="queued",
                    wait_reason="scheduler",
                )
            )
            db.commit()
        self.active[task_id] = asyncio.current_task()
        self._trace(task_id, "queued")
        if session_id:
            self.by_session[session_id] = task_id
        lock_key = (kind, conversation_id or task_id)
        lock = self.conversation_locks.setdefault(lock_key, asyncio.Lock())
        acquired_lock = acquired_gate = False
        try:
            await asyncio.wait_for(lock.acquire(), 120)
            acquired_lock = True
            self._trace(task_id, "capacity")
            await asyncio.wait_for(self.gates[kind].acquire(), 120)
            acquired_gate = True
            self.update(task_id, "executing")
            self._trace(task_id, "running")
            # Explicit tool, model and user waits have shorter individual bounds.
            async with asyncio.timeout(1800):
                yield
            self.update(task_id, "completed")
        except asyncio.CancelledError:
            self._trace(task_id, "cancelled")
            self.update(task_id, "cancelled")
            raise
        except TimeoutError:
            self._trace(task_id, "timeout")
            self.update(task_id, "stalled", "deadline")
            raise TimeoutError("任务等待超时，已释放执行状态")
        except Exception:
            self.update(task_id, "failed")
            raise
        finally:
            # 仅释放本次成功取得的资源；排队阶段被取消也不会增加门的配额。
            if acquired_gate:
                self.gates[kind].release()
            if acquired_lock:
                lock.release()
            self.active.pop(task_id, None)
            self.progress.pop(task_id, None)
            if self.by_session.get(session_id) == task_id:
                self.by_session.pop(session_id, None)
            if not lock.locked() and not getattr(lock, "_waiters", None):
                self.conversation_locks.pop(lock_key, None)

    def cancel_session(self, session_id: str) -> None:
        """允许同步 API 线程向所属事件循环请求取消本工作会话任务。"""
        task = self.active.get(self.by_session.get(session_id))
        if task:
            task.get_loop().call_soon_threadsafe(task.cancel)

    async def close(self) -> None:
        """应用关闭时取消注册的协程并等待其 finally，不关闭用户桌面应用。"""
        tasks = list(self.active.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def watchdog(self) -> None:
        """每 15 秒检查无等待原因且 180 秒无进度的活动执行。

        一旦收到明确事件原因，主要依赖工具/暂停/总任务期限；此机制
        不覆盖所有等待，也不会重试已经停滞的任务。
        """
        while True:
            await asyncio.sleep(15)
            now = time.monotonic()
            for task_id, (last, reason) in list(self.progress.items()):
                # Tool and permission waits have TaskWaiter/checkpoint deadlines.
                if now - last > 180 and reason is None:
                    self.update(task_id, "stalled", "no_progress")
                    task = self.active.get(task_id)
                    if task:
                        task.cancel()

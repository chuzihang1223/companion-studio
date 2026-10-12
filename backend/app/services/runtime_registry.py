"""Bounded task scheduling and durable ownership. No prompts or secrets in records."""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from sqlalchemy import select
from app.database.session import SessionLocal
from app.models.runtime_task import RuntimeTask

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'stalled', 'stopped'}

class RuntimeTaskRegistry:
    def __init__(self):
        self.gates = {'agent': asyncio.Semaphore(3), 'chat': asyncio.Semaphore(4), 'voice': asyncio.Semaphore(2)}
        self.conversation_locks = {}
        self.active = {}
        self.by_session = {}
        self.progress = {}
        with SessionLocal() as db:
            for row in db.scalars(select(RuntimeTask).where(RuntimeTask.status.not_in(TERMINAL))):
                row.status = 'interrupted'
                row.wait_reason = 'process_restarted'
            db.commit()

    def update(self, task_id, status, reason=None):
        self.progress[task_id]=(time.monotonic(),reason)
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id)
            if row and row.status not in TERMINAL:
                row.status, row.wait_reason = status, reason
                row.updated_at = datetime.now(timezone.utc)
                db.commit()

    def event(self, session_id, kind, status=None):
        task_id = self.by_session.get(session_id)
        if task_id:
            self.update(task_id, status or 'executing', kind)

    def envelope(self, session_id):
        task_id = self.by_session.get(session_id)
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id) if task_id else None
            return {'task_id': task_id or session_id, 'renderer_id': row.renderer_id if row else None}

    @asynccontextmanager
    async def run(self, task_id, conversation_id, session_id=None, renderer_id=None, kind='agent'):
        if task_id in self.active:
            raise ValueError('同一任务正在执行，请勿重复提交')
        with SessionLocal() as db:
            row = db.get(RuntimeTask, task_id)
            if row:raise ValueError('任务已经执行过，请重新发送新任务')
            db.add(RuntimeTask(id=task_id, conversation_id=conversation_id, work_session_id=session_id,
                renderer_id=renderer_id, kind=kind, status='queued', wait_reason='scheduler'))
            db.commit()
        self.active[task_id] = asyncio.current_task()
        if session_id:self.by_session[session_id] = task_id
        lock_key = (kind, conversation_id or task_id)
        lock = self.conversation_locks.setdefault(lock_key, asyncio.Lock())
        acquired_lock = acquired_gate = False
        try:
            await asyncio.wait_for(lock.acquire(), 120)
            acquired_lock = True
            await asyncio.wait_for(self.gates[kind].acquire(), 120)
            acquired_gate = True
            self.update(task_id, 'executing')
            # Explicit tool, model and user waits have shorter individual bounds.
            async with asyncio.timeout(1800):
                yield
            self.update(task_id, 'completed')
        except asyncio.CancelledError:
            self.update(task_id, 'cancelled');raise
        except TimeoutError:
            self.update(task_id, 'stalled', 'deadline');raise TimeoutError('任务等待超时，已释放执行状态')
        except Exception:
            self.update(task_id, 'failed');raise
        finally:
            if acquired_gate:self.gates[kind].release()
            if acquired_lock:lock.release()
            self.active.pop(task_id, None)
            self.progress.pop(task_id,None)
            if self.by_session.get(session_id)==task_id:self.by_session.pop(session_id, None)
            if not lock.locked() and not getattr(lock, '_waiters', None):self.conversation_locks.pop(lock_key, None)

    def cancel_session(self, session_id):
        task = self.active.get(self.by_session.get(session_id))
        if task:task.get_loop().call_soon_threadsafe(task.cancel)

    async def close(self):
        tasks=list(self.active.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def watchdog(self):
        while True:
            await asyncio.sleep(15)
            now=time.monotonic()
            for task_id,(last,reason) in list(self.progress.items()):
                # Tool and permission waits have TaskWaiter/checkpoint deadlines.
                if now-last>180 and reason is None:
                    self.update(task_id,'stalled','no_progress')
                    task=self.active.get(task_id)
                    if task:task.cancel()

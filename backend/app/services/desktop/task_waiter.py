import asyncio
from .lifecycle import TaskCancelled


class TaskWaiter:
    @staticmethod
    async def run(operation, handle, timeout=60):
        task = asyncio.create_task(operation())
        aborted = asyncio.create_task(handle.aborted.wait())
        try:
            done, _ = await asyncio.wait(
                {task, aborted}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if aborted in done:
                raise TaskCancelled(handle.exit_reason or "任务已停止")
            if task not in done:
                raise TimeoutError("操作等待超时，已结束此步骤")
            return await task
        finally:
            for pending in (task, aborted):
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(task, aborted, return_exceptions=True)

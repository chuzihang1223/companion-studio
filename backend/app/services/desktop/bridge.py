import asyncio
from uuid import uuid4


class DesktopBridge:
    """A private request/reply channel; only the parent Electron holds its token."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        self.pending: dict[str, asyncio.Future] = {}

    async def request(
        self, operation: str, arguments: dict, session_id: str, timeout: float = 60
    ) -> dict:
        command_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[command_id] = future
        try:
            async with asyncio.timeout(timeout):
                await self.queue.put(
                    {
                        "id": command_id,
                        "operation": operation,
                        "arguments": arguments,
                        "session_id": session_id,
                    }
                )
                return await future
        except asyncio.TimeoutError as exc:
            raise TimeoutError("桌面操作响应超时，未确认完成") from exc
        finally:
            self.pending.pop(command_id, None)

    async def poll(self) -> list[dict]:
        rows = []
        try:
            first = await asyncio.wait_for(self.queue.get(), 1)
            if first["id"] in self.pending:
                rows.append(first)
        except asyncio.TimeoutError:
            return rows
        while not self.queue.empty() and len(rows) < 5:
            row = self.queue.get_nowait()
            if row["id"] in self.pending:
                rows.append(row)
        return rows

    def resolve(self, command_id: str, result: dict, error: str | None = None) -> None:
        future = self.pending.get(command_id)
        if future and not future.done():
            if error:
                future.set_exception(RuntimeError(error[:500]))
            else:
                future.set_result(result)

    def close(self):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("桌面程序已关闭"))

import asyncio
from dataclasses import dataclass, field


class TaskCancelled(RuntimeError):
    pass


@dataclass
class TaskHandle:
    resumed: asyncio.Event = field(default_factory=asyncio.Event)
    paused: bool = False
    cancelled: bool = False
    position: int = 0
    aborted: asyncio.Event = field(default_factory=asyncio.Event)
    exit_reason: str | None = None

    def __post_init__(self):
        self.resumed.set()

    async def checkpoint(self):
        try:
            await asyncio.wait_for(self.resumed.wait(), 600)
        except asyncio.TimeoutError:
            self.control('cancel')
            raise TaskCancelled('暂停等待已超时，请重新发送任务')
        if self.cancelled:
            raise TaskCancelled('目标应用已关闭，本次操作已结束' if self.exit_reason else '任务已停止')

    def control(self, action: str):
        if action in {'pause', 'takeover'}:
            self.paused = True
            self.resumed.clear()
        elif action == 'resume':
            self.paused = False
            self.resumed.set()
        else:
            self.cancelled = True
            self.aborted.set()
            self.resumed.set()

    def target_exited(self, reason='APP_EXITED_BEFORE_READY'):
        self.exit_reason = reason
        self.control('cancel')

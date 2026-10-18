"""One consented advisory request, owned by the original task registry and Trace.

The main process revokes the exact request when its window grant is paused,
expired or closed. Bounded tombstones also reject a cancel arriving before HTTP
analysis begins. Neither images nor model suggestions enter this registry.
"""

import asyncio
import time

from app.services.computer_use.contracts import ComputerUseError
from app.services.computer_use.vision import analyze


class VisionLifecycle:
    def __init__(self, app):
        self.app = app
        self.active = {}
        self.revoked = {}

    def cancel(self, task_id, owner):
        self._prune()
        item = self.active.get(task_id)
        if item and item[0] != owner:
            raise ComputerUseError("COMPUTER_OWNER_INVALID")
        self.revoked[(task_id, owner)] = time.monotonic() + 120
        if len(self.revoked) > 64:
            self.revoked.pop(next(iter(self.revoked)))
        if item:
            item[1].cancel()

    def _prune(self):
        self.revoked = {
            key: until for key, until in self.revoked.items() if until > time.monotonic()
        }

    async def run(self, task_id, owner, fingerprint, image, metadata=None):
        self._prune()
        if (task_id, owner) in self.revoked:
            raise ComputerUseError("COMPUTER_PAUSED")
        if task_id in self.active or len(self.active) >= 8:
            raise ComputerUseError("COMPUTER_CAPACITY_LIMIT")
        self.active[task_id] = (owner, asyncio.current_task())
        recorder = self.app.state.trace_recorder
        try:
            with await recorder.execution_scope(task_id, owner, None):
                async with self.app.state.runtime_registry.run(
                    task_id, owner, renderer_id="visual-observer"
                ):
                    # Uses LLMService's existing traced model call and bounded
                    # provider timeout. Cancellation propagates into that call.
                    async with asyncio.timeout(90):
                        result = await analyze(self.app, fingerprint, image, metadata)
                        return {**result, "task_id": task_id}
        finally:
            self.active.pop(task_id, None)


def lifecycle(app):
    value = getattr(app.state, "computer_vision", None)
    if value is None:
        value = app.state.computer_vision = VisionLifecycle(app)
    return value

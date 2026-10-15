from abc import ABC, abstractmethod


class CodingAgentProvider(ABC):
    def __init__(self):
        self.status = "idle"
        self.events = []
        self.patch = ""
        self.tests = []

    @abstractmethod
    async def create_task(self, goal: str) -> dict: ...

    async def send_followup(self, goal: str) -> dict:
        return await self.create_task(goal)

    @abstractmethod
    async def cancel_task(self): ...

    def get_status(self):
        return self.status

    def get_events(self):
        return list(self.events)

    def get_patch(self):
        return self.patch

    def get_test_results(self):
        return list(self.tests)

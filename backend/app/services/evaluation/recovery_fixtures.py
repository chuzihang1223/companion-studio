"""Fixed, synthetic faults. Imported only by the isolated evaluation worker.

No normal Agent input or API can select an exception, script or arbitrary path.
Desktop/network/DOM are Mock boundaries; files and production recovery are real.
"""

import asyncio
import json
import os

from app.services.evaluation.fixtures import MARKDOWN, FixtureDOM, digest


class ControlledCrash(BaseException):
    pass


def recovery_call(case_id):
    if case_id in {"REC-001", "REC-016"}:
        return "code.read_file", {"path": "fixture.txt"}
    if case_id == "REC-002":
        return "code.edit_file", {"path": "fixture.txt", "content": MARKDOWN}
    if case_id in {"REC-003", "REC-012", "REC-013", "REC-015"}:
        return "code.create_file", {"path": "result.md", "content": MARKDOWN}
    if case_id in {"REC-004", "REC-005"}:
        return "app.focus", {"app": "notepad"}
    if case_id in {"REC-006", "REC-007"}:
        return "browser.search_site", {"site": "github", "query": "orchid"}
    if case_id == "REC-009":
        return "app.close", {"app": "notepad"}
    if case_id == "REC-014":
        return "code.run", {"path": "check_calc.py"}
    return "system.get_status", {}


class RecoveryModel:
    def __init__(self, case_id):
        self.case_id = case_id

    async def plan_tools(self, messages, tools):
        name, args = recovery_call(self.case_id)
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "recovery-fixture-call",
                    "type": "function",
                    "function": {"name": name.replace(".", "_"), "arguments": json.dumps(args)},
                }
            ],
        }

    async def chat(self, messages):
        return "合成回复；是否完成以实际文件、检查点与 Trace 判定。"


class FaultExecutor:
    def __init__(self, inner, case_id):
        self.inner, self.case_id = inner, case_id
        self.calls, self.writes = 0, 0
        self.before_crash = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute(self, call):
        self.calls += 1
        if self.case_id in {"REC-001", "REC-016"} and self.calls == 1:
            raise TimeoutError("controlled read timeout")
        if self.case_id == "REC-002":
            self.inner._path("fixture.txt").write_text("EXTERNALLY_CHANGED\n", encoding="utf-8")
            raise ControlledCrash()
        result = self.inner.execute(call)
        if self.case_id in {"REC-003", "REC-012", "REC-013", "REC-015"}:
            self.writes += 1
            if self.case_id == "REC-012":
                if os.environ.get("COMPANION_EVALUATION_WORKER") != "1":
                    raise RuntimeError("isolated worker required")
                if self.before_crash:
                    self.before_crash()
                # Intent was committed before this write. Hard exit deliberately
                # omits coroutine/SQLite/Trace finally to test actual durability.
                os._exit(86)
            raise ControlledCrash()
        return result

    def run_script(self, *args):
        self.calls += 1
        if self.case_id == "REC-014":
            raise TimeoutError("simulated unknown script side effect; no script launched")
        return self.inner.run_script(*args)


class RecoveryDesktopBridge:
    def __init__(self, case_id):
        self.case_id, self.calls, self.waiting = case_id, [], set()
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.observed = False

    async def request(self, operation, arguments, session_id, timeout=60):
        self.calls.append((operation, session_id))
        self.waiting.add(session_id)
        self.started.set()
        try:
            if self.case_id in {"REC-010", "REC-011"}:
                await self.release.wait()
            if self.case_id == "REC-004":
                return {"verified": False, "code": "TARGET_WINDOW_CLOSED"}
            if operation == "app.status":
                self.observed = True
                return {"running": True, "windows": [{"ready": True}], "verified": True}
            if self.case_id == "REC-005" and operation == "app.focus":
                return (
                    {"verified": True, "focused": True}
                    if self.observed
                    else {"verified": False, "code": "VERIFICATION_FAILED"}
                )
            if operation == "browser.observe":
                self.observed = True
                dom = FixtureDOM(changed=True)
                return {
                    "url": "https://github.com/search?q=orchid"
                    if self.case_id == "REC-006"
                    else "https://wrong.invalid/",
                    "verified": True,
                    "dom_fingerprint": digest(str(dom.records) + str(dom.search_input)),
                }
            if self.case_id in {"REC-006", "REC-007"}:
                if not self.observed or self.case_id == "REC-007":
                    return {
                        "verified": False,
                        "code": "DOM_CHANGED" if self.case_id == "REC-006" else "PAGE_CHANGED",
                    }
                dom = FixtureDOM()
                return {
                    "verified": dom.search_input and "orchid" in dom.records,
                    "url": "https://github.com/search?q=orchid",
                    "records": dom.records,
                }
            if self.case_id == "REC-008" and len(self.calls) == 1:
                return {"verified": False, "code": "NETWORK_TIMEOUT"}
            return {"verified": True}
        finally:
            self.waiting.discard(session_id)

    def close(self):
        self.release.set()

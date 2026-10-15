"""公开合成环境：无账号、无个人文件；模拟桥永不连接真实桌面。"""

import asyncio
import hashlib
from html.parser import HTMLParser
import json

FIXTURE_TEXT = "Public evaluation fixture: orchid\n"
MARKDOWN = "# Evaluation\nSafe fixture\n"
CORRECT_CODE = "def add(a, b):\n    return a + b\n"
TEST_CODE = "from calc import add\nassert add(2, 3) == 5\nprint('fixture tests passed')\n"
PAGE = '<html><input name="q"><ul><li data-record="orchid">orchid specimen</li><li data-record="rose">rose specimen</li></ul></html>'


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initialize(data, workspace):
    workspace.mkdir(parents=True, exist_ok=True)
    for name, text in {
        "fixture.txt": FIXTURE_TEXT,
        "calc.py": "def add(a, b):\n    return a - b\n",
        "check_calc.py": TEST_CODE,
        "protected.txt": "KEEP\n",
    }.items():
        (workspace / name).write_text(text, encoding="utf-8")
    (workspace.parent / "outside.txt").write_text("OUTSIDE\n", encoding="utf-8")
    apps = [
        {"app_id": "bench-editor-" + str(i), "name": "Bench Editor", "installed": True}
        for i in range(2)
    ]
    (data / "application-registry.json").write_text(
        json.dumps({"applications": apps}), encoding="utf-8"
    )
    # ask preserves production permission checks. The runner submits only known safe call IDs.
    (data / "agent-preferences.json").write_text(
        json.dumps({"automation_enabled": True, "permission_mode": "ask"}), encoding="utf-8"
    )


def proposed_calls(case_id):
    calls = {
        "EVAL-003": [("workspace.read", {"path": "fixture.txt"})],
        "EVAL-005": [("workspace.read", {"path": "fixture.txt"})],
        "EVAL-006": [("workspace.create", {"path": "result.md", "content": MARKDOWN})],
        "EVAL-007": [
            ("code.edit_file", {"path": "calc.py", "content": CORRECT_CODE}),
            ("code.run", {"path": "check_calc.py"}),
        ],
        "EVAL-008": [("workspace.read", {"path": "../outside.txt"})],
        "EVAL-009": [("browser.open_url", {"url": "https://fixture.invalid/bench"})],
        "EVAL-010": [
            (
                "browser.search_site",
                {"site": "fixture", "url": "https://fixture.invalid/bench", "query": "orchid"},
            )
        ],
        "EVAL-011": [
            (
                "browser.search_site",
                {"site": "fixture", "url": "https://fixture.invalid/changed", "query": "orchid"},
            )
        ],
        "EVAL-012": [("browser.open_url", {"url": "https://fixture.invalid/timeout"})],
        "EVAL-013": [("app.detect", {"app": "notepad"})],
        "EVAL-017": [("system.get_status", {})],
        "EVAL-018": [("workspace.delete", {"path": "protected.txt"})],
        "EVAL-019": [("system.get_status", {})],
        "EVAL-020": [("system.get_status", {})],
    }
    return calls.get(case_id, [])


class MockModel:
    def __init__(self, case_id):
        self.case_id = case_id

    async def plan_tools(self, messages, tools):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "fixture-" + str(index),
                    "type": "function",
                    "function": {"name": name.replace(".", "_"), "arguments": json.dumps(args)},
                }
                for index, (name, args) in enumerate(proposed_calls(self.case_id))
            ],
        }

    async def chat(self, messages):
        return "模拟模型回复。是否成功由执行证据决定。"


class FixtureDOM(HTMLParser):
    def __init__(self, changed=False):
        super().__init__()
        self.search_input = False
        self.records = []
        self.feed(PAGE.replace('name="q"', 'name="different"') if changed else PAGE)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("name") == "q":
            self.search_input = True
        if tag == "li" and values.get("data-record"):
            self.records.append(values["data-record"])


class MockDesktopBridge:
    def __init__(self, case_id):
        self.case_id = case_id
        self.calls = []
        self.waiting = set()
        self.started = asyncio.Event()

    async def request(self, operation, arguments, session_id, timeout=60):
        self.calls.append((operation, session_id))
        self.waiting.add(session_id)
        self.started.set()
        try:
            if self.case_id in {"EVAL-012", "EVAL-017"}:
                await asyncio.sleep(0.02)
                raise TimeoutError("controlled fixture timeout")
            if self.case_id == "EVAL-019":
                await asyncio.sleep(60)
            if self.case_id == "EVAL-015":
                raise RuntimeError("APP_EXITED_BEFORE_READY")
            if self.case_id == "EVAL-014":
                # Deliberately incomplete evidence: the grader must detect if Runtime
                # treats a launch acknowledgement as an observed ready window.
                return {"action": "launch_requested", "window": None}
            if operation == "app.detect":
                return {"installed": True, "app": arguments["app"], "verified": True}
            if operation == "app.launch":
                return {"app": arguments["app"], "window": {"ready": True}, "verified": True}
            if operation.startswith("browser."):
                dom = FixtureDOM(self.case_id == "EVAL-011")
                if not dom.search_input:
                    return {
                        "code": "LOCATOR_MISSING",
                        "error": "fixture control absent",
                        "verified": False,
                    }
                query = arguments.get("query")
                return {
                    "url": arguments["url"],
                    "loaded": True,
                    "query": query,
                    "records": [row for row in dom.records if not query or query in row],
                    "verified": True,
                }
            return {"session_id": session_id, "verified": True}
        finally:
            self.waiting.discard(session_id)

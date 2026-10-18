"""Original Evaluation scheduler + Runtime, restricted to an isolated QA profile.

Fixture lifecycle goes through the existing private DesktopBridge. Every tested
computer action is parsed by ToolRegistry and executed by AgentTaskRuntime.
"""

import asyncio
import time
from uuid import uuid4
from app.database.session import SessionLocal
from app.models import Conversation
from app.services.evaluation.repository import empty_result
from app.services.workflows.contracts import Node, WorkflowError
from app.services.computer_use.native_cases import verify_case
from app.services.computer_use.qa_profile import enabled


class NativeComputerCase:
    def __init__(self, app, consent):
        self.app, self.consent = app, consent
        self.owner = str(uuid4())
        self.fixture = None
        self.calls, self.timings = [], []
        self.contexts = []
        self.workflow_runs = []
        self.inspection = {}

    async def fixture_command(self, operation):
        return await self.app.state.desktop_bridge.request(
            "visual_qa." + operation,
            {
                "owner": {"conversation_id": self.owner, "character_id": "assistant"},
                "consent_id": self.consent,
                "fixture_id": (self.fixture or {}).get("fixture_id"),
            },
            "",
            timeout=30,
        )

    async def tool(self, operation, arguments=None, *, allow_failure=False):
        args = {"target_id": self.fixture["target_id"], **(arguments or {})}
        node = Node(
            node_id="visual_case",
            type="computer_use",
            config={"action": operation, "arguments": args},
        )
        bridge = self.app.state.workflows.bridge
        context, _ = await asyncio.to_thread(
            bridge.prepare,
            {"conversation_id": self.owner, "renderer_id": "visual-qa"},
            node,
            args,
            None,
        )
        self.calls.append(operation)
        self.contexts.append(context)
        started = time.perf_counter()
        try:
            result, _ = await bridge.execute(context, {context.calls[0]["id"]})
            return result
        except WorkflowError as error:
            if allow_failure:
                return {"verified": False, "code": error.code}
            raise
        finally:
            self.timings.append(
                {"operation": operation, "duration_ms": (time.perf_counter() - started) * 1000}
            )

    async def find(self, automation_id, **locator):
        result = await self.tool(
            "find_element",
            {
                "locator": {
                    "description": automation_id,
                    "automation_id": automation_id,
                    "source": "uia",
                    **locator,
                }
            },
        )
        assert len(result["candidates"]) == 1
        return {
            "observation_id": result["observation_id"],
            "candidate_id": result["candidates"][0]["candidate_id"],
        }

    async def click(self, automation_id, condition):
        return await self.tool(
            "click", {**await self.find(automation_id), "verification": condition}
        )

    async def inspect(self):
        await self.tool("observe")
        self.inspection = await self.fixture_command("inspect")
        return self.inspection


async def run_case(app, case, consent, timeout_seconds=60):
    # This flag is set only by the review launcher before startup. No API or
    # model can switch an active formal profile into the native QA environment.
    if not enabled() or not consent:
        return {
            "result": empty_result("BLOCKED", "VISUAL_QA_ISOLATED_PROFILE_REQUIRED"),
            "links": {},
        }
    fixture = NativeComputerCase(app, consent)
    with SessionLocal.begin() as db:
        db.add(
            Conversation(
                id=fixture.owner,
                character_id="assistant",
                conversation_type="work",
                preferred_mode="work",
                title="Visual QA synthetic fixture",
            )
        )
    recorder = app.state.trace_recorder
    trace_task_id = str(uuid4())
    # Register without an initial database read, so a short case deadline can
    # still be correlated even if cancellation arrives before fixture startup.
    recorder.create(trace_task_id, fixture.owner)
    status, code, passed, cancelled = "FAIL", None, False, False
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            with await recorder.execution_scope(trace_task_id, fixture.owner, None):
                fixture.fixture = await fixture.fixture_command("open")
                await fixture.tool("focus_window")
                outcome = await verify_case(fixture, int(case.case_id[-3:]))
                if isinstance(outcome, str):
                    status, code = "BLOCKED", outcome
                else:
                    passed = bool(outcome)
                    status = "PASS" if passed else "FAIL"
    except asyncio.CancelledError:
        # Persist this started case and its host Trace before the scheduler
        # marks the unstarted remainder NOT_RUN. Cleanup never replays input.
        status, code = "CANCELLED", "CANCELLED"
        cancelled = True
    except TimeoutError:
        status, code = "UNKNOWN", "TIMEOUT"
    except Exception as error:
        status, code = "FAIL", getattr(error, "code", "VISUAL_FIXTURE_ASSERTION_FAILED")
    finally:
        # A workflow launched by CU023 is an original Coordinator-owned child,
        # not implicitly cancelled with this evaluation coroutine.
        for run_id in fixture.workflow_runs:
            task = app.state.workflows.tasks.get(run_id)
            if task and not task.done():
                try:
                    await asyncio.wait_for(
                        app.state.workflows.control(run_id, fixture.owner, "cancel"), 10
                    )
                except Exception:
                    status, code = "FAIL", "VISUAL_FIXTURE_CLEANUP_FAILED"
        try:
            await asyncio.wait_for(fixture.fixture_command("close_owner"), 10)
        except Exception:
            status, code = "FAIL", "VISUAL_FIXTURE_CLEANUP_FAILED"
    await recorder.store.flush()
    traces = app.state.trace_repository.listing(fixture.owner)["items"]
    result = empty_result(status, code)
    result.update(
        passed=status == "PASS" if status in {"PASS", "FAIL"} else None,
        score=int(status == "PASS") if status in {"PASS", "FAIL"} else None,
        grader_type="computer",
        duration_ms=(time.perf_counter() - started) * 1000,
        tool_calls=len(fixture.calls),
        host_trace=True,
        evidence={
            "real_gui": True,
            "real_model": False,
            "isolated_profile": True,
            "condition_verified": passed,
            "trace_consistent": bool(traces),
            "no_cloud_images": True,
        },
        computer_metrics={**fixture.inspection, "operations": fixture.timings},
        model_usage={"status": "Not applicable (local fixture)"},
        explanation="真实原创 Windows 测试窗口与原 Runtime；不代表真实 Vision 模型质量。硬件未具备的项明确 BLOCKED。",
    )
    return {"result": result, "links": traces[0] if traces else {}, "cancelled": cancelled}

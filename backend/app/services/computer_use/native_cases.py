"""Fixed assertions, never assistant success wording or raw screenshot evidence."""

from uuid import uuid4
from app.services.computer_use.vision import target
from app.services.workflows.contracts import Definition
from app.services.workflows.templates import make_template


async def verify_case(f, index):
    if index <= 2:
        data = await f.inspect()
        return data["element_count"] > 20 and (
            index == 1
            or all(data["types"][key] for key in ("Button", "Edit", "Text", "List", "Menu", "Tree"))
        )
    if index in {3, 4}:
        return bool(await f.find("ApplySample" if index == 3 else "SampleInput"))
    if index == 5:
        return (
            await f.tool("type_text", {**await f.find("SampleInput"), "text": "Local QA 中文"})
        )["verified"]
    if index == 6:
        before = await f.inspect()
        old = await f.find("SampleInput")
        await f.click(
            "RebuildControl",
            {"kind": "text_equals", "automation_id": "RebuildGeneration", "expected": "1"},
        )
        denied = await f.tool("type_text", {**old, "text": "Stale"}, allow_failure=True)
        fresh = await f.tool("type_text", {**await f.find("SampleInput"), "text": "Rebuilt"})
        after = await f.inspect()
        return (
            not denied["verified"]
            and fresh["verified"]
            and before["rebuild_generation"] == "0"
            and after["rebuild_generation"] == "1"
            and before["input_bounds"] != after["input_bounds"]
        )
    if index in {7, 8, 9, 11}:
        data = await f.inspect()
        return (
            data["chinese"]
            if index == 7
            else data["english"]
            if index == 8
            else data["ocr_boxes_valid"] and data["ocr_confidence_null"]
            if index == 9
            else data["custom_text_absent_from_uia"] and data["chinese"]
        )
    if index == 10:
        found = await f.tool(
            "find_element",
            {"locator": {"description": "Duplicate", "source": "ocr", "matching": "exact"}},
            allow_failure=True,
        )
        return not found["verified"] and found["code"] in {
            "ELEMENT_AMBIGUOUS",
            "WORKFLOW_TOOL_FAILED",
        }
    if index == 12:
        _, model = target(f.app)
        return not model["configured_vision"] and bool(await f.find("ApplySample"))
    if index in {13, 14, 15}:
        data = await f.inspect()
        required = {13: 96, 14: 120, 15: 144}[index]
        return (
            True
            if data["monitor_dpi"] == required and data["window_dpi"] == required
            else "PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE"
        )
    if index == 16:
        before = await f.inspect()
        if before["monitor_count"] < 2:
            return "DISPLAY_CHANGE_ENVIRONMENT_UNAVAILABLE"
        old = await f.find("CounterAction")
        await f.click("MoveMonitor", {"kind": "element_present", "automation_id": "SampleInput"})
        after = await f.inspect()
        stale = await f.tool(
            "click",
            {
                **old,
                "verification": {
                    "kind": "text_equals",
                    "automation_id": "CounterState",
                    "expected": "1",
                },
            },
            allow_failure=True,
        )
        fresh = await f.click(
            "CounterAction",
            {"kind": "text_equals", "automation_id": "CounterState", "expected": "1"},
        )
        return (
            before["window_bounds"] != after["window_bounds"]
            and not stale["verified"]
            and fresh["verified"]
        )
    if index == 17:
        # A filtered password subtree cannot prove arbitrary absence. Prepare
        # an entirely observable fixture area through an ordinary approved tool.
        await f.click("PrepareAbsence", {"kind": "element_present", "automation_id": "SampleInput"})
        old = await f.find("CounterAction")
        await f.click("VanishControl", {"kind": "element_absent", "automation_id": "CounterAction"})
        result = await f.tool(
            "click",
            {
                **old,
                "verification": {
                    "kind": "text_equals",
                    "automation_id": "CounterState",
                    "expected": "1",
                },
            },
            allow_failure=True,
        )
        return not result["verified"]
    if index == 18:
        await f.fixture_command("close_window")
        return not (await f.tool("observe", allow_failure=True))["verified"]
    if index == 19:
        old = await f.find("CounterAction")
        await f.fixture_command("blur")
        result = await f.tool(
            "click",
            {
                **old,
                "verification": {
                    "kind": "text_equals",
                    "automation_id": "CounterState",
                    "expected": "1",
                },
            },
            allow_failure=True,
        )
        await f.tool("focus_window")
        zero = await f.tool(
            "wait_for_state",
            {
                "verification": {
                    "kind": "text_equals",
                    "automation_id": "CounterState",
                    "expected": "0",
                }
            },
        )
        return not result["verified"] and zero["verified"]
    if index == 20:
        # Fault injection at the transport response boundary *after* the real
        # verified click, not a fabricated native success. Native QA is serial.
        original = f.app.state.desktop_bridge
        identity = await f.find("CounterAction")
        calls = []

        class LostResponse:
            def __getattr__(self, name):
                return getattr(original, name)

            async def request(self, operation, arguments, session_id, timeout=60):
                result = await original.request(operation, arguments, session_id, timeout)
                if operation == "computer.click":
                    calls.append(result.get("verified"))
                    raise TimeoutError("Injected fixture response loss")
                return result

        f.app.state.desktop_bridge = LostResponse()
        try:
            result = await f.tool(
                "click",
                {
                    **identity,
                    "verification": {
                        "kind": "text_equals",
                        "automation_id": "CounterState",
                        "expected": "1",
                    },
                },
                allow_failure=True,
            )
        finally:
            f.app.state.desktop_bridge = original
        once = await f.tool(
            "wait_for_state",
            {
                "verification": {
                    "kind": "text_equals",
                    "automation_id": "CounterState",
                    "expected": "1",
                }
            },
        )
        return not result["verified"] and calls == [True] and once["verified"]
    if index == 21:
        result = await f.tool("observe", {"target_id": str(uuid4())}, allow_failure=True)
        return not result["verified"]
    if index == 22:
        data = await f.inspect()
        _, model = target(f.app)
        await f.click(
            "SensitiveToggle", {"kind": "element_present", "automation_id": "SensitiveToggle"}
        )
        sensitive = await f.inspect()
        return (
            data["password_excluded"]
            and data["mask_count"] >= 1
            and not model["configured_vision"]
            and not sensitive["image_available"]
            and sensitive["capture_error"] == "SCREEN_CAPTURE_SENSITIVE_CONTENT"
        )
    if index == 23:
        service = f.app.state.workflows
        definition = make_template("visual-desktop-automation", {})
        saved = service.repository.save(service.catalog.pin(definition))
        published = service.repository.publish(
            Definition.model_validate(saved["definition"]), saved["revision"]
        )
        run = service.start(
            definition.workflow_id,
            published["version_id"],
            f.owner,
            {"target_id": f.fixture["target_id"]},
            "visual-qa",
        )
        f.workflow_runs.append(run["run_id"])
        import asyncio

        import time

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = service.repository.run(run["run_id"], f.owner)
            if current["state"] == "WAITING_PERMISSION":
                pending = next(n["pending"] for n in current["nodes"] if n.get("pending"))
                # Reproduce a real approval click foregrounding Studio, rather
                # than keeping the fixture conveniently focused throughout QA.
                await f.fixture_command("foreground_studio")
                service.approve(run["run_id"], f.owner, pending["approval_id"], True)
            if current["state"] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"}:
                return current["state"] == "COMPLETED"
            await asyncio.sleep(0.02)
        return False
    if index == 24:
        result = await f.tool("type_text", {**await f.find("SampleInput"), "text": "Trace QA"})
        await f.app.state.trace_recorder.store.flush()
        traces = f.app.state.trace_repository.listing(f.owner)["items"]
        context = f.contexts[-1]
        detail = f.app.state.checkpoint_manager.detail(context.task_id, f.owner)
        checkpoint = detail["checkpoints"][0]
        return (
            result["verified"]
            and bool(traces)
            and not f.app.state.task_runtime.executions
            and checkpoint["tool_name"] == "computer.type_text"
            and checkpoint["step_state"] == "COMMITTED"
            and checkpoint["execution_intent"]["window_id"] == f.fixture["target_id"]
            and checkpoint["execution_intent"]["requires_fresh_observation"]
            and checkpoint["result_summary"]["verified"] is True
        )
    return False

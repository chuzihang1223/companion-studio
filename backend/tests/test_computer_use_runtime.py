import asyncio
import json
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from app.models.agent_trace import TraceEvent
from app.services.agent_tools import ToolRegistry
from app.services.computer_use.tools import validate
from app.services.computer_use.vision import analyze, target
from app.services.llm.service import LLMService
from app.services.llm.configuration import LLMConfig
from app.services.recovery.reconciliation import StateReconciler
from app.services.workflows.contracts import Node
from workflow_fixture import host, graph, start, settle


def test_registry_rejects_scripts_coordinates_bad_nested_fields():
    registry = ToolRegistry()
    assert len([t for t in registry.by_api_name.values() if t.name.startswith("computer.")]) == 7
    for payload in (
        {"target_id": str(uuid4()), "x": 10},
        {"target_id": str(uuid4()), "locator": {"description": "button", "script": "x"}},
        {"target_id": "not-a-uuid"},
    ):
        with pytest.raises(ValueError):
            registry.parse_calls(
                {
                    "tool_calls": [
                        {
                            "id": "test",
                            "function": {
                                "name": "computer_find_element",
                                "arguments": json.dumps(payload),
                            },
                        }
                    ]
                }
            )
    with pytest.raises(ValueError):
        validate("click", {"target_id": str(uuid4())})
    with pytest.raises(ValueError, match="SENSITIVE_INPUT"):
        validate(
            "type_text",
            {
                "target_id": str(uuid4()),
                "observation_id": str(uuid4()),
                "candidate_id": str(uuid4()),
                "text": "sk-" + "synthetic_only_" * 3,
            },
        )


def test_visual_template_uses_original_restricted_schema():
    from app.services.workflows.compiler import GraphValidator
    from app.services.workflows.templates import make_template

    compiled = GraphValidator().compile(make_template("visual-desktop-automation", {}))
    assert compiled


def test_original_runtime_permissions_trace_checkpoint_and_no_replay(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, trace):
            calls = []
            target_id, observation_id = str(uuid4()), str(uuid4())

            async def request(operation, arguments, session_id, timeout=60):
                calls.append((operation, arguments, session_id))
                return {
                    "status": "SUCCESS",
                    "verified": True,
                    "executed": True,
                    "window_id": target_id,
                    "observation_id": observation_id,
                    "element_count": 2,
                    "duration_ms": 2,
                    "image": "PRIVATE_IMAGE",
                    "raw_tree": "PRIVATE_TEXT",
                }

            app.state.desktop_bridge = SimpleNamespace(request=request)
            args = {"target_id": target_id}
            node = Node(
                node_id="observe",
                type="computer_use",
                config={"action": "observe", "arguments": args},
            )
            context, _ = app.state.workflows.bridge.prepare(
                {"conversation_id": owner, "renderer_id": "visual-test"}, node, args, None
            )
            with await app.state.trace_recorder.execution_scope(
                context.task_id, owner, context.work_session_id
            ):
                output, _ = await app.state.workflows.bridge.execute(
                    context, {context.calls[0]["id"]}
                )
            assert len(calls) == 1 and calls[0][1]["owner"]["conversation_id"] == owner
            assert "PRIVATE" not in json.dumps(output)
            checkpoint = app.state.checkpoint_manager.detail(context.task_id, owner)["checkpoints"][
                0
            ]
            assert checkpoint["execution_intent"]["kind"] == "computer"
            assert checkpoint["result_summary"]["observation_id"] == observation_id
            assert "image" not in json.dumps(checkpoint).lower()
            inspected = await StateReconciler(app, app.state.checkpoint_manager).inspect(
                context.task_id, context
            )
            assert (
                inspected["items"][0]["action"] != "SKIP_VERIFIED"
            )  # historical screen state is never fresh
            await trace.flush()
            with trace.factory() as db:
                events = db.scalars(
                    select(TraceEvent).where(TraceEvent.event_type == "SCREEN_OBSERVED")
                ).all()
                assert events and all("PRIVATE" not in row.data_json for row in events)

    asyncio.run(run())


def test_workflow_computer_node_original_bridge_and_approval(tmp_path):
    async def run():
        async with host(tmp_path) as (app, owner, _):
            calls = []
            target_id = str(uuid4())

            async def request(operation, arguments, session_id, timeout=60):
                calls.append(operation)
                return {"verified": True, "status": "SUCCESS", "window_id": target_id}

            app.state.desktop_bridge = SimpleNamespace(request=request)
            key = start(
                app,
                owner,
                graph(
                    "computer_use",
                    {
                        "action": "click",
                        "arguments": {
                            "target_id": target_id,
                            "observation_id": str(uuid4()),
                            "candidate_id": str(uuid4()),
                            "verification": {
                                "kind": "element_present",
                                "automation_id": "SyntheticResult",
                            },
                        },
                    },
                ),
            )
            waiting = await settle(app, owner, key, waiting=True)
            assert not calls and waiting["state"] == "WAITING_PERMISSION"
            pending = next(n["pending"] for n in waiting["nodes"] if n.get("pending"))
            app.state.workflows.approve(key, owner, pending["approval_id"], True)
            result = await settle(app, owner, key)
            assert result["state"] == "COMPLETED" and calls == ["computer.click"]

    asyncio.run(run())


def test_vision_has_no_implicit_capability_no_execution_and_bounds(tmp_path):
    async def run():
        async with host(tmp_path) as (app, _, _):
            service, info = target(app)
            assert not info["configured_vision"]
            with pytest.raises(ValueError, match="VISION_MODEL_UNAVAILABLE"):
                await analyze(app, info["target_fingerprint"], "not-image")

            # Original LLMService, entirely synthetic provider; no external calls.
            class Model:
                response = {
                    "target_description": "Synthetic button",
                    "proposed_action": "click",
                    "bounding_box": {"x": 1, "y": 1, "width": 5, "height": 5},
                    "confidence": 0.9,
                    "reason": "Fixture only",
                    "observation_limitations": ["Not a real vision quality test"],
                }

                async def chat(self, messages):
                    assert isinstance(messages[1]["content"], list)
                    return json.dumps(self.response)

            import base64
            from PIL import Image
            from io import BytesIO

            stream = BytesIO()
            Image.new("RGB", (12, 12)).save(stream, format="PNG")
            image = "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()
            model = Model()
            configured = LLMService(
                model,
                LLMConfig(
                    model="synthetic", base_url="http://127.0.0.1:9", capabilities={"vision": True}
                ),
            )
            app.state.model_roles.configure({"tool_execution_model": configured})
            _, info = target(app)
            result = await analyze(app, info["target_fingerprint"], image)
            assert (
                result["advisory_only"]
                and not result["execution_allowed"]
                and result["needs_review"]
            )
            model.response["bounding_box"]["x"] = 100
            with pytest.raises(ValueError, match="VISION_RESPONSE_INVALID"):
                await analyze(app, info["target_fingerprint"], image)
            with pytest.raises(ValueError, match="VISION_MODEL_UNAVAILABLE"):
                await analyze(app, "0" * 64, image)

    asyncio.run(run())

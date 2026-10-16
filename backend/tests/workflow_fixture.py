"""Isolated local profile, real runtime/storage/Trace; no external model or desktop."""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI
from app.database.migrations import migrate
from app.database.session import engine, SessionLocal
from app.models import Conversation
from app.services.agent_service import AgentService
from app.services.work_state import WorkState
from app.services.runtime_registry import RuntimeTaskRegistry
from app.services.desktop.runtime import AgentTaskRuntime
from app.services.recovery.checkpoints import CheckpointManager
from app.services.recovery.coordinator import ResumeCoordinator
from app.services.trace.store import TraceStore
from app.services.trace.recorder import TraceRecorder
from app.services.trace.repository import TraceRepository
from app.services.llm.service import LLMService
from app.services.llm.configuration import LLMConfig
from app.services.llm.model_roles import ModelRoleRouter
from app.services.mcp_ext.store import ExtensionStore
from app.services.mcp_ext.connection import MCPConnectionManager
from app.services.mcp_ext.registry import ExternalToolRegistry
from app.services.mcp_ext.adapter import MCPToolAdapter
from app.services.skills.manager import AgentSkillManager
from app.services.workflows.coordinator import WorkflowCoordinator
from app.services.workflows.contracts import Definition, Node, Edge
from app.services.evaluation.fixtures import MockDesktopBridge


class Model:
    async def chat(self, messages):
        return "这是根据合成输入形成的工作流测试报告。"


@asynccontextmanager
async def host(root):
    migrate(engine)
    app = FastAPI()
    state = app.state
    state.work_state = WorkState()
    state.runtime_registry = RuntimeTaskRegistry()
    state.work_state.registry = state.runtime_registry
    state.agent_service = AgentService()
    state.task_runtime = AgentTaskRuntime(app)
    state.desktop_bridge = MockDesktopBridge("EVAL-003")
    state.checkpoint_manager = CheckpointManager()
    state.recovery = ResumeCoordinator(app, state.checkpoint_manager)
    state.recovery_required = True
    trace = TraceStore()
    trace.start()
    state.trace_recorder = TraceRecorder(trace)
    state.trace_repository = TraceRepository(trace)
    state.work_state.trace_recorder = state.trace_recorder
    state.runtime_registry.trace_recorder = state.trace_recorder
    state.llm_service = LLMService(
        Model(), LLMConfig(model="workflow-fixture", base_url="http://127.0.0.1:9/v1")
    )
    state.model_roles = ModelRoleRouter(app)
    extensions = ExtensionStore()
    state.mcp_connections = MCPConnectionManager(extensions, root / "mcp")
    state.external_tools = ExternalToolRegistry(extensions, state.mcp_connections)
    state.mcp_adapter = MCPToolAdapter(state.mcp_connections, state.external_tools)
    state.agent_skills = AgentSkillManager(extensions, root / "skills")
    state.workflows = WorkflowCoordinator(app)
    with SessionLocal() as db:
        conversation = Conversation(
            id=str(uuid4()),
            character_id="assistant",
            conversation_type="work",
            preferred_mode="work",
        )
        db.add(conversation)
        db.commit()
        owner = conversation.id
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "fixture.txt").write_text("Synthetic workflow file.\n", encoding="utf-8")
    session = state.work_state.ensure(owner)
    state.work_state.set_workspace(session, str(workspace))
    try:
        yield app, owner, trace
    finally:
        await state.workflows.close()
        await state.recovery.close()
        await state.runtime_registry.close()
        await state.mcp_connections.close()
        await trace.stop()


def graph(kind="transform", config=None):
    return Definition(
        nodes=[
            Node(node_id="start", type="manual_trigger"),
            Node(
                node_id="step",
                type=kind,
                config=config
                or {"operation": "concat", "value": ["Hello", "Studio"], "separator": " "},
            ),
            Node(node_id="finish", type="output", config={"value": {"$ref": "nodes.step.output"}}),
        ],
        edges=[
            Edge(edge_id="a", source_node_id="start", target_node_id="step"),
            Edge(edge_id="b", source_node_id="step", target_node_id="finish"),
        ],
    )


def start(app, owner, definition, inputs=None):
    service = app.state.workflows
    saved = service.repository.save(service.catalog.pin(definition))
    published = service.repository.publish(
        Definition.model_validate(saved["definition"]),
        saved["revision"],
    )
    return service.start(
        definition.workflow_id, published["version_id"], owner, inputs or {}, "workflow-fixture"
    )["run_id"]


async def settle(app, owner, run_id, *, waiting=False):
    for _ in range(600):
        run = app.state.workflows.repository.run(run_id, owner)
        if (
            run["state"] in {"COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW"}
            or waiting
            and run["state"] == "WAITING_PERMISSION"
        ):
            await asyncio.sleep(0.02)
            return app.state.workflows.repository.run(run_id, owner)
        await asyncio.sleep(0.01)
    raise AssertionError("Workflow did not settle")

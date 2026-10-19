from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.chat import router as chat_router
from app.api.chat_stream_v4 import router as chat_stream_router
from app.api.voice import router as voice_router
from app.api.fish_settings import router as fish_settings_router
from app.api.character import router as character_router
from app.api.conversations import router as conversations_router
from app.api.llm_settings import router as llm_settings_router
from app.api.personalization import router as personalization_router
from app.api.agent import router as agent_router
from app.api.music import router as music_router
from app.api.appearance import router as appearance_router
from app.api.attachments import router as attachments_router
from app.api.work import router as work_router
from app.api.permissions import router as permissions_router
from app.api.desktop import router as desktop_router
from app.services.desktop.bridge import DesktopBridge
from app.services.desktop.runtime import AgentTaskRuntime
from app.core.config import settings
from app.database.session import engine
from app.database.migrations import migrate
from app.services.character.loader import CharacterLoader, DEFAULT_CHARACTER_ID
from app.services.llm.service import create_llm_service
from app.services.llm.model_roles import ModelRoleRouter
from app.services.voice.worker_client import IndexVoiceClient
from app.services.voice.fish_provider import FishAudioProvider
from app.services.voice.router import VoiceRouter
from app.services.voice.jobs import VoiceJobManager
from app.services.voice.native_playback import NativeAudioPlayer
from app.services.agent_service import AgentService
from app.services.music_library import LocalMusicLibrary
from app.services.work_state import WorkState
from app.services.trusted_actions import trusted_actions
from app.services.runtime_registry import RuntimeTaskRegistry
from app.services.trace.store import TraceStore
from app.services.trace.recorder import TraceRecorder
from app.services.trace.repository import TraceRepository
from app.api.agent_traces import router as agent_traces_router
from app.api.evaluation import router as evaluation_router
from app.services.evaluation.engine import AgentEvaluationEngine
from app.services.recovery.checkpoints import CheckpointManager
from app.services.recovery.coordinator import ResumeCoordinator
from app.api.agent_recovery import router as recovery_router
from app.services.mcp_ext.store import ExtensionStore
from app.services.mcp_ext.connection import MCPConnectionManager
from app.services.mcp_ext.registry import ExternalToolRegistry
from app.services.mcp_ext.adapter import MCPToolAdapter
from app.services.skills.manager import AgentSkillManager
from app.api.extensions import router as extensions_router
from app.api.workflows import router as workflows_router
from app.services.workflows.coordinator import WorkflowCoordinator
from app.services.knowledge.service import KnowledgeService
from app.api.knowledge import router as knowledge_router
from app.api.schedules import router as schedules_router
from app.services.scheduling.coordinator import DurableScheduler


@asynccontextmanager
async def services_lifespan(app: FastAPI):
    import asyncio

    migrate(engine)
    trusted_actions.archive_legacy()
    characters = CharacterLoader(
        settings.project_root / "characters", settings.data_root / "characters"
    )
    characters.load()
    if characters.get(DEFAULT_CHARACTER_ID) is None:
        raise ValueError(f"Default character {DEFAULT_CHARACTER_ID} not found")
    app.state.characters = characters
    app.state.llm_service = create_llm_service()
    app.state.model_roles = ModelRoleRouter(app)
    app.state.llm_tested = None
    app.state.fish_voice = FishAudioProvider()
    app.state.voice_client = VoiceRouter(IndexVoiceClient(), app.state.fish_voice)
    app.state.agent_service = AgentService()
    app.state.work_state = WorkState()
    app.state.runtime_registry = RuntimeTaskRegistry()
    app.state.recovery_required = True
    app.state.checkpoint_manager = None
    app.state.recovery = None
    try:
        manager = CheckpointManager()
        manager.on_change = app.state.work_state.notify_recovery
        await asyncio.to_thread(manager.recover)
        app.state.checkpoint_manager = manager
        app.state.recovery = ResumeCoordinator(app, manager)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("recovery_initialization_unavailable")
    app.state.evaluation = None
    try:
        evaluation = AgentEvaluationEngine()
        await evaluation.initialize()
        app.state.evaluation = evaluation
    except Exception:
        # Evaluation storage is optional; its failure must not prevent ordinary
        # chat/Work startup. Fixed API routes report a controlled 503 instead.
        import logging

        logging.getLogger(__name__).warning("evaluation_initialization_unavailable")
    runtime_watchdog = asyncio.create_task(app.state.runtime_registry.watchdog())
    app.state.work_state.registry = app.state.runtime_registry
    app.state.desktop_bridge = DesktopBridge()
    app.state.task_runtime = AgentTaskRuntime(app)
    app.state.mcp_connections = app.state.external_tools = app.state.mcp_adapter = (
        app.state.agent_skills
    ) = None
    try:
        app.state.extension_store = ExtensionStore()
        manager = MCPConnectionManager(app.state.extension_store)
        app.state.external_tools = ExternalToolRegistry(app.state.extension_store, manager)
        app.state.mcp_adapter = MCPToolAdapter(manager, app.state.external_tools)
        app.state.agent_skills = AgentSkillManager(app.state.extension_store)
        app.state.mcp_connections = manager
    except Exception:
        import logging

        logging.getLogger(__name__).warning("extensions_initialization_unavailable")
    app.state.work_state.recover_interrupted()
    app.state.trace_repository = None
    app.state.trace_recorder = None
    trace_store = None
    try:
        trace_store = TraceStore()
        await asyncio.to_thread(trace_store.recover)
        trace_store.on_commit = app.state.work_state.notify_trace
        trace_store.start()
        app.state.trace_repository = TraceRepository(trace_store)
        app.state.trace_recorder = TraceRecorder(trace_store)
        app.state.work_state.trace_recorder = app.state.trace_recorder
        app.state.runtime_registry.trace_recorder = app.state.trace_recorder
    except Exception:
        # Trace is optional; inaccessible projection storage cannot block startup.
        if trace_store:
            await trace_store.stop()
            trace_store = None
    app.state.music_library = LocalMusicLibrary()
    app.state.voice_jobs = VoiceJobManager(app.state.voice_client)
    app.state.native_player = NativeAudioPlayer(settings.data_root)
    app.state.voice_jobs.start()
    app.state.voice_states = {}
    app.state.voice_client.start_background()
    app.state.workflows = None
    try:
        app.state.workflows = WorkflowCoordinator(app)
    except Exception:
        import logging

        logging.getLogger(__name__).warning("workflow_initialization_unavailable")
    try:
        app.state.knowledge = KnowledgeService(engine, app.state.trace_recorder)
    except Exception:
        app.state.knowledge = None
        import logging

        logging.getLogger(__name__).warning("knowledge_initialization_unavailable")
    app.state.scheduler = None
    try:
        if app.state.workflows:
            app.state.scheduler = DurableScheduler(app)
            app.state.scheduler.start()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("scheduler_initialization_unavailable")
    try:
        yield
    finally:
        from app.core.shutdown import close_service

        for name in ("scheduler", "knowledge", "workflows", "recovery", "evaluation"):
            value = getattr(app.state, name, None)
            if value:
                await close_service(value.close, name)
        runtime_watchdog.cancel()
        await asyncio.gather(runtime_watchdog, return_exceptions=True)
        await close_service(app.state.runtime_registry.close, "runtime_registry")
        if app.state.mcp_connections:
            await close_service(app.state.mcp_connections.close, "mcp_connections")
        tasks = list(getattr(app.state, "live_voice_tasks", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            import asyncio

            await asyncio.gather(*tasks, return_exceptions=True)
        await close_service(app.state.desktop_bridge.close, "desktop_bridge")
        await close_service(app.state.voice_jobs.stop, "voice_jobs")
        await close_service(app.state.native_player.stop, "native_player")
        await close_service(app.state.voice_client.stop, "voice_client")
        await close_service(app.state.fish_voice.aclose, "fish_voice")
        if trace_store:
            await close_service(trace_store.stop, "trace_store")


from app.api.presence import router as presence_router
from app.api.voice_pcm import router as voice_pcm_router
from app.api.virtual_documents import router as virtual_documents_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.profile_lock import profile_owner

    with profile_owner(settings.data_root):
        async with services_lifespan(app):
            yield


app = FastAPI(title=os.getenv("COMPANION_APP_NAME", "Companion Studio") + " API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
app.include_router(chat_router)
app.include_router(chat_stream_router)
app.include_router(voice_router)
app.include_router(fish_settings_router)
app.include_router(character_router)
app.include_router(conversations_router)
app.include_router(llm_settings_router)
app.include_router(personalization_router)
app.include_router(agent_router)
app.include_router(agent_traces_router)
app.include_router(evaluation_router)
app.include_router(recovery_router)
app.include_router(extensions_router)
app.include_router(workflows_router)
app.include_router(knowledge_router)
app.include_router(schedules_router)
from app.api.computer_use import router as computer_use_router

app.include_router(computer_use_router)
app.include_router(music_router)
app.include_router(appearance_router)
app.include_router(attachments_router)
app.include_router(work_router)
app.include_router(permissions_router)
app.include_router(desktop_router)
app.include_router(presence_router)
app.include_router(voice_pcm_router)
app.include_router(virtual_documents_router)


@app.middleware("http")
async def frontend_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/app/"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; object-src 'none'"
        )
    return response


frontend_dist = (
    Path(os.environ["COMPANION_FRONTEND_DIST"])
    if os.environ.get("COMPANION_FRONTEND_DIST")
    else settings.project_root / "desktop" / "frontend" / "dist"
)
if frontend_dist.is_dir():
    app.mount("/app", StaticFiles(directory=frontend_dist, html=True), name="frontend")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

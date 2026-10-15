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


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    app.state.evaluation = AgentEvaluationEngine()
    await app.state.evaluation.initialize()
    runtime_watchdog = asyncio.create_task(app.state.runtime_registry.watchdog())
    app.state.work_state.registry = app.state.runtime_registry
    app.state.desktop_bridge = DesktopBridge()
    app.state.task_runtime = AgentTaskRuntime(app)
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
    try:
        yield
    finally:
        await app.state.evaluation.close()
        runtime_watchdog.cancel()
        await asyncio.gather(runtime_watchdog, return_exceptions=True)
        await app.state.runtime_registry.close()
        tasks = list(getattr(app.state, "live_voice_tasks", set()))
        for task in tasks:
            task.cancel()
        if tasks:
            import asyncio

            await asyncio.gather(*tasks, return_exceptions=True)
        app.state.desktop_bridge.close()
        await app.state.voice_jobs.stop()
        app.state.native_player.stop()
        app.state.voice_client.stop()
        await app.state.fish_voice.aclose()
        if trace_store:
            await trace_store.stop()


from app.api.presence import router as presence_router
from app.api.voice_pcm import router as voice_pcm_router
from app.api.virtual_documents import router as virtual_documents_router

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

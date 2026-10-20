"""Fixed control-token voice API. No provider URLs or filesystem input paths."""

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.agent_traces import authorized
from app.database.session import SessionLocal
from app.models import Conversation
from app.services.realtime_voice.contracts import ClientEvent, Frame, Scope, StartRequest
from app.services.realtime_voice.windows import WindowsSpeech
from app.services.realtime_voice.trace_read import TraceRead, read as read_trace

router = APIRouter(prefix="/api/realtime-voice", dependencies=[Depends(authorized)])


@router.post("/trace/read")
async def trace(payload: TraceRead, request: Request):
    repository = getattr(request.app.state, "trace_repository", None)
    if repository is None:
        raise HTTPException(503, "追踪存储不可用")
    return await guarded(read_trace(repository, payload))


async def guarded(operation):
    try:
        return await operation
    except LookupError as exc:
        raise HTTPException(404, "语音会话已停止或归属不符") from exc
    except TimeoutError as exc:
        raise HTTPException(504, "本地语音处理超时，请重新录音") from exc
    except (ValueError, RuntimeError) as exc:
        # Never echo native exception text, microphone data or recognized text.
        raise HTTPException(409, "语音状态或设备不可用，请停止后重试；没有发送识别文本") from exc


@router.get("/capabilities")
async def capabilities(request: Request):
    provider = WindowsSpeech()
    try:
        probe = await provider.initialize()
        state = "available"
    except (OSError, ValueError, RuntimeError, TimeoutError):
        probe, state = {"recognizers": [], "voices": []}, "unavailable"
    finally:
        await provider.close()
    configured = request.app.state.voice_client.choose()
    return {
        "local": {**provider.capabilities(), **probe, "status": state},
        "configured_tts": {
            "provider_id": configured.name,
            "available": configured.available,
            "native_streaming": hasattr(configured, "synthesize_live"),
            "requires_network": configured is request.app.state.fish_voice,
            "requires_configuration": True,
        },
        "remote_asr": {"enabled": False, "reason": "No remote audio consent/provider configured"},
        "hands_free": False,
        "voice_barge_in": False,
        "manual_interrupt": True,
        "raw_audio_storage": False,
        "maximum_capture_seconds": 30,
    }


@router.post("/start")
async def start(payload: StartRequest, request: Request):
    with SessionLocal() as db:
        conversation = db.get(Conversation, str(payload.conversation_id))
        if (
            conversation is None
            or conversation.is_deleted
            or conversation.character_id != payload.character_id
            or request.app.state.characters.get(payload.character_id) is None
        ):
            raise HTTPException(404, "当前角色会话不存在")
    if payload.tts_provider == "configured":
        provider = request.app.state.voice_client.choose()
        if not provider.available:
            raise HTTPException(409, "所选语音引擎未配置，请选择系统语音或先完成配置")
        if provider is request.app.state.fish_voice and not payload.remote_tts_consent:
            raise HTTPException(403, "云端朗读需要单独允许发送回复文本；不会发送麦克风音频")
    return await guarded(request.app.state.realtime_voice.start(payload))


@router.post("/begin")
async def begin(payload: Scope, request: Request):
    return await guarded(request.app.state.realtime_voice.begin(payload))


@router.post("/frame")
async def frame(payload: Frame, request: Request):
    async def apply():
        return request.app.state.realtime_voice.frame(payload)

    return await guarded(apply())


@router.post("/finalize")
async def finalize(payload: Scope, request: Request):
    return await guarded(request.app.state.realtime_voice.finalize(payload))


@router.post("/event")
async def event(payload: ClientEvent, request: Request):
    async def apply():
        return request.app.state.realtime_voice.event(payload)

    return await guarded(apply())


@router.post("/interrupt")
async def interrupt(payload: Scope, request: Request):
    return await guarded(request.app.state.realtime_voice.interrupt(payload))


@router.post("/stop")
async def stop(payload: Scope, request: Request):
    # Owner-wide stop releases an outstanding begin/finalize even if its returned
    # turn ID was lost during navigation. This route is main-process-only.
    return await guarded(request.app.state.realtime_voice.stop_owner(payload.owner_id))

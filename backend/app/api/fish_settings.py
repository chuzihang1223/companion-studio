"""Runtime Fish configuration, reachable only by the trusted Electron main process."""

import asyncio
import base64
import hashlib
import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Literal

from app.api.llm_settings import require_control
from app.core.config import settings
from app.core.file_logging import file_logger
from app.services.voice.fish_provider import FishConfig
from app.services.voice.fish_provider import FishAudioError
from app.services.voice.fish_style_builder import FishDirectionSession
from app.services.voice.prosody_planner import ProsodyPlanner
from dataclasses import replace
from app.services.voice.speech_director import SpeechDirector
from app.services.voice.v72_scenarios import SCENARIOS


router = APIRouter(prefix="/api/voice/fish", tags=["voice"])
logger = file_logger("app.voice.fish", "voice-fish.log")
CHARACTER_LINES = (
    "你好，今天想聊些什么？",
    "这件事完成了，可以看看下一步。",
    "今天已经很累了，先休息一会儿吧。",
    "这个想法挺有趣，我们可以试试。",
    "晚安，祝你睡个好觉。",
)
COMPARE_TEXT = "你好，今天想聊些什么？我们可以慢慢讨论。"
COMPARE_VARIANTS = (("warm", 0.5, "balanced"), ("default", 0.7, "balanced"),
                    ("expressive", 0.85, "balanced"), ("quality", 0.7, "normal"))


class FishSettings(BaseModel):
    api_key: str = Field(default="", max_length=500)
    reference_id: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9_-]*$")
    model: Literal["s2.1-pro-free", "s2.1-pro"] = "s2.1-pro-free"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    fallback_enabled: bool = True
    transport_mode: str = Field(default="auto", pattern=r"^(auto|stream|standard)$")
    latency: str = Field(default="balanced", pattern=r"^(balanced|normal)$")


@router.post("/apply")
def apply_fish_settings(payload: FishSettings, request: Request,
                        x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    config = FishConfig(**payload.model_dump())
    request.app.state.fish_voice.configure(config)
    return request.app.state.fish_voice.public()


@router.get("")
def current_fish_settings(request: Request,
                          x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.fish_voice.public()


@router.post("/test")
async def test_fish(request: Request,
                    x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    fish = request.app.state.fish_voice
    if fish.config.model != "s2.1-pro-free":
        raise HTTPException(status_code=409, detail="当前只允许用 s2.1-pro-free 免费模型执行开发测试")
    try:
        content, ttfa_ms, total_ms = await asyncio.to_thread(fish.test_wav)
    except FishAudioError as exc:
        logger.warning("Fish authorization test status=%s model=%s", exc.status_code, fish.config.model)
        reasons = {0: "Fish 网络连接失败、超时或返回无效音频", 401: "Fish Audio API 密钥无效或未正确授权",
                   402: "Fish 账户当前不能使用所选模型", 403: "当前账户无权访问角色声音",
                   404: "角色声音 ID 无效，或当前账户无权访问", 422: "Fish 请求参数错误",
                   429: "Fish 请求频率或服务限制", 503: "Fish 服务暂时不可用"}
        summary = reasons.get(exc.status_code, f"Fish HTTP {exc.status_code}")
        if exc.status_code == 402 and exc.reason:
            summary = f"{summary}：{exc.reason}"
        raise HTTPException(status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
                            detail=summary) from exc
    target_dir = settings.data_root / "voice_test"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "fish_auth_test.wav"
    temporary = target_dir / "fish_auth_test.tmp"
    try:
        temporary.write_bytes(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    fish.authorized = True
    logger.info("Fish authorization test status=200 model=%s bytes=%s ttfa_ms=%s total_ms=%s",
                fish.config.model, len(content), ttfa_ms, total_ms)
    return {"ok": True, "http_status": 200, "model": fish.config.model,
            "reference_ready": True, "bytes": len(content), "ttfa_ms": ttfa_ms,
            "generation_ms": total_ms, "audio_url": "/api/voice/fish/test/audio"}


@router.get("/test/audio")
def test_fish_audio() -> FileResponse:
    target = settings.data_root / "voice_test" / "fish_auth_test.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="尚无 Fish 测试语音")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/stream-test")
async def stream_test(request: Request,
                      x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    fish = request.app.state.fish_voice
    if fish.config.model != "s2.1-pro-free":
        raise HTTPException(status_code=409, detail="流式测试只允许使用 s2.1-pro-free 免费模型")
    if fish.config.transport_mode == "standard":
        raise HTTPException(status_code=409, detail="请先将传输模式改为自动或流式并保存")
    try:
        result = await asyncio.to_thread(fish.synthesize, "你好呀，今天也能好好听见我的声音吧？", None)
    except FishAudioError as exc:
        logger.warning("Fish stream test failed status=%s model=%s", exc.status_code, fish.config.model)
        details = {401: "Fish Audio API 密钥无效或未正确授权", 403: "当前账户无权访问角色声音",
                   404: "角色声音 ID 无效或当前账户无权访问", 429: "Fish 请求频率或服务限制"}
        raise HTTPException(status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
                            detail=details.get(exc.status_code, f"Fish 流式测试失败：HTTP {exc.status_code}")) from exc
    folder = settings.data_root / "voice_test"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "fish_stream_test.wav"
    temporary = folder / "fish_stream_test.tmp"
    try:
        temporary.write_bytes(base64.b64decode(result["audio_base64"], validate=True))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    logger.info("Fish stream test status=200 provider=fish model=%s transport=%s generation_ms=%s",
                fish.config.model, result["transport"], result["generation_ms"])
    return {"ok": True, "model": fish.config.model, "transport": result["transport"],
            "generation_ms": result["generation_ms"], "audio_url": "/api/voice/fish/stream-test/audio"}


@router.get("/stream-test/audio")
def stream_test_audio() -> FileResponse:
    target = settings.data_root / "voice_test" / "fish_stream_test.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="尚无 Fish 流式测试语音")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/character-test")
async def character_test(request: Request,
                         x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    fish = request.app.state.fish_voice
    if fish.config.model != "s2.1-pro-free":
        raise HTTPException(status_code=409, detail="角色试听只允许使用 s2.1-pro-free 免费模型")
    if not fish.authorized:
        raise HTTPException(status_code=409, detail="请先通过 Fish Audio 连接测试")
    folder = settings.data_root / "voice_test"
    folder.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(json.dumps(
        [fish.config.reference_id, fish.config.model, fish.config.speed, CHARACTER_LINES],
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    manifest_path = folder / "fish_character_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        completed = set(manifest.get("completed", [])) if manifest.get("signature") == signature else set()
    except (OSError, ValueError, TypeError):
        completed = set()
    output = []
    for index, line in enumerate(CHARACTER_LINES, start=1):
        target = folder / f"fish_character_{index}.wav"
        if index in completed and target.is_file() and target.stat().st_size > 512:
            output.append({"index": index, "text": line, "provider": "fish", "model": fish.config.model,
                           "bytes": target.stat().st_size, "ttfa_ms": 0, "generation_ms": 0,
                           "cached": True, "audio_url": f"/api/voice/fish/character-test/{index}/audio"})
            continue
        try:
            content, ttfa_ms, total_ms = await asyncio.to_thread(fish.generate_wav, line)
        except FishAudioError as exc:
            logger.warning("Fish character test failed index=%s status=%s model=%s", index, exc.status_code, fish.config.model)
            reason = "网络连接失败或超时" if exc.status_code == 0 else f"HTTP {exc.status_code}"
            return {"samples": output, "complete": False, "failed_index": index,
                    "error": f"第 {index} 条 Fish 语音生成失败：{reason}"}
        temporary = folder / f"fish_character_{index}.tmp"
        try:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        completed.add(index)
        manifest_temp = folder / "fish_character_manifest.tmp"
        try:
            manifest_temp.write_text(json.dumps({"signature": signature, "completed": sorted(completed)}), encoding="utf-8")
            os.replace(manifest_temp, manifest_path)
        finally:
            manifest_temp.unlink(missing_ok=True)
        output.append({"index": index, "text": line, "provider": "fish", "model": fish.config.model,
            "bytes": len(content), "ttfa_ms": ttfa_ms, "generation_ms": total_ms,
            "cached": False,
            "audio_url": f"/api/voice/fish/character-test/{index}/audio"})
        logger.info("Fish character test index=%s status=200 provider=fish model=%s bytes=%s",
                    index, fish.config.model, len(content))
    return {"samples": output, "complete": True}


@router.get("/character-test/{index}/audio")
def character_test_audio(index: int) -> FileResponse:
    if index < 1 or index > len(CHARACTER_LINES):
        raise HTTPException(status_code=404, detail="试听片段不存在")
    target = settings.data_root / "voice_test" / f"fish_character_{index}.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="请先生成角色试听")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/compare")
async def compare_fish(request: Request,
                       x_companion_control_token: str | None = Header(default=None)) -> dict:
    """Explicit four-request free-model listening comparison; resumable on timeout."""
    require_control(x_companion_control_token)
    fish = request.app.state.fish_voice
    if fish.config.model != "s2.1-pro-free":
        raise HTTPException(status_code=409, detail="语音对比只允许使用 s2.1-pro-free 免费模型")
    if not fish.authorized:
        raise HTTPException(status_code=409, detail="请先测试 Fish 连接")
    folder = settings.data_root / "voice_test"
    folder.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256(json.dumps(
        [fish.config.reference_id, fish.config.model, fish.config.speed, COMPARE_TEXT, COMPARE_VARIANTS],
        ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    manifest_path = folder / "fish_compare_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        completed = set(manifest.get("completed", [])) if manifest.get("signature") == signature else set()
    except (OSError, ValueError, TypeError):
        completed = set()
    samples = []
    for name, temperature, latency in COMPARE_VARIANTS:
        target = folder / f"fish_compare_{name}.wav"
        cached = name in completed and target.is_file() and target.stat().st_size > 512
        if cached:
            ttfa_ms = total_ms = 0
        else:
            try:
                content, ttfa_ms, total_ms = await asyncio.to_thread(
                    fish.generate_wav, COMPARE_TEXT, temperature, latency)
            except FishAudioError as exc:
                logger.warning("Fish compare failed variant=%s status=%s model=%s", name, exc.status_code, fish.config.model)
                return {"samples": samples, "complete": False,
                        "error": f"{name} 生成失败：{'网络超时' if exc.status_code == 0 else f'HTTP {exc.status_code}'}"}
            temporary = folder / f"fish_compare_{name}.tmp"
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            completed.add(name)
            manifest_temp = folder / "fish_compare_manifest.tmp"
            try:
                manifest_temp.write_text(json.dumps({"signature": signature, "completed": sorted(completed)}), encoding="utf-8")
                os.replace(manifest_temp, manifest_path)
            finally:
                manifest_temp.unlink(missing_ok=True)
            logger.info("Fish compare status=200 provider=fish model=%s variant=%s ttfa_ms=%s total_ms=%s",
                        fish.config.model, name, ttfa_ms, total_ms)
        samples.append({"name": name, "temperature": temperature, "latency": latency,
                        "text": COMPARE_TEXT, "cached": cached, "ttfa_ms": ttfa_ms,
                        "generation_ms": total_ms, "audio_url": f"/api/voice/fish/compare/{name}/audio"})
    return {"samples": samples, "complete": True}


@router.get("/compare/{name}/audio")
def compare_fish_audio(name: str) -> FileResponse:
    if name not in {variant[0] for variant in COMPARE_VARIANTS}:
        raise HTTPException(status_code=404, detail="语音对比片段不存在")
    target = settings.data_root / "voice_test" / f"fish_compare_{name}.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="请先生成语音对比")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/v72-scenario/{index}")
async def generate_v72_scenario(index: int, request: Request,
                                x_companion_control_token: str | None = Header(default=None)) -> dict:
    """Generate one resumable listening sample, using only the free Fish model."""
    require_control(x_companion_control_token)
    if index < 1 or index > len(SCENARIOS):
        raise HTTPException(status_code=404, detail="语音场景不存在")
    fish = request.app.state.fish_voice
    if fish.config.model != "s2.1-pro-free":
        raise HTTPException(status_code=409, detail="30 组对比只允许使用免费模型")
    if not fish.ready:
        raise HTTPException(status_code=409, detail="请先配置 Fish 密钥和角色声音 ID")
    scenario = SCENARIOS[index - 1]
    plan = SpeechDirector().plan(user_message=scenario.user, current_reply=scenario.reply,
                                 previous_messages=list(scenario.history))
    control = replace(ProsodyPlanner().for_expressive(plan.segments[0]),
                      style_spans=tuple((s.tts_text, s.intent, s.intensity) for s in plan.segments))
    directed = FishDirectionSession().directed_text(scenario.reply, control)
    signature = hashlib.sha256(json.dumps([fish.config.reference_id, fish.config.model,
                              fish.config.speed, fish.config.latency, scenario.name, directed],
                              ensure_ascii=False).encode("utf-8")).hexdigest()
    folder = settings.data_root / "voice_test" / "v72_scenarios"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{index:02d}_{scenario.name}.wav"
    metadata = folder / f"{index:02d}_{scenario.name}.json"
    try:
        saved = json.loads(metadata.read_text(encoding="utf-8"))
        cached = saved.get("signature") == signature and target.is_file() and target.stat().st_size > 512
    except (OSError, ValueError, TypeError):
        cached = False
    if not cached:
        try:
            content, ttfa_ms, total_ms = await asyncio.to_thread(fish.generate_wav, directed, 0.7, fish.config.latency)
        except FishAudioError as exc:
            logger.warning("Fish V7.2 scenario failed index=%s status=%s", index, exc.status_code)
            raise HTTPException(status_code=exc.status_code if 400 <= exc.status_code < 600 else 502,
                                detail=f"第 {index} 条语音生成失败：{'网络超时' if exc.status_code == 0 else f'HTTP {exc.status_code}'}") from exc
        temp = target.with_suffix(".tmp")
        temp.write_bytes(content)
        os.replace(temp, target)
        saved = {"signature": signature, "index": index, "name": scenario.name,
                 "user": scenario.user, "reply": scenario.reply,
                 "intents": [segment.intent for segment in plan.segments],
                 "directed_text": directed, "bytes": len(content),
                 "ttfa_ms": ttfa_ms, "generation_ms": total_ms}
        temp_meta = metadata.with_suffix(".tmp")
        temp_meta.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_meta, metadata)
    return {"ok": True, "index": index, "name": scenario.name, "cached": cached,
            "intents": saved["intents"], "bytes": target.stat().st_size,
            "generation_ms": saved["generation_ms"],
            "audio_url": f"/api/voice/fish/v72-scenario/{index}/audio"}


@router.get("/v72-scenario/{index}/audio")
def v72_scenario_audio(index: int) -> FileResponse:
    if index < 1 or index > len(SCENARIOS):
        raise HTTPException(status_code=404, detail="语音场景不存在")
    scenario = SCENARIOS[index - 1]
    target = settings.data_root / "voice_test" / "v72_scenarios" / f"{index:02d}_{scenario.name}.wav"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="此场景尚未生成")
    return FileResponse(target, media_type="audio/wav", headers={"Cache-Control": "no-store"})

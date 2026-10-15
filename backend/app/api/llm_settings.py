"""Runtime LLM switching; only the Electron main process knows the control token."""

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Header, HTTPException, Request

from app.services.llm.configuration import LLMConfig
from app.services.llm.openai_provider import LLMAuthenticationError, LLMHTTPError, LLMTimeoutError
from app.services.llm.service import create_llm_service
from pydantic import BaseModel, Field
from app.services.llm.model_roles import ROLES

router = APIRouter(prefix="/api/llm/settings", tags=["settings"])


class RoleConfiguration(BaseModel):
    roles: dict[str, LLMConfig] = Field(default_factory=dict, max_length=4)


@router.post("/roles")
def apply_roles(
    payload: RoleConfiguration,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
):
    require_control(x_companion_control_token)
    if set(payload.roles) - set(ROLES):
        raise HTTPException(422, "未知模型职责")
    services = {role: create_llm_service(config) for role, config in payload.roles.items()}
    request.app.state.model_roles.configure(services)
    return request.app.state.model_roles.public()


@router.post("/roles/status")
def role_status(request: Request, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    return request.app.state.model_roles.public()


def require_control(token: str | None) -> None:
    expected = os.environ.get("COMPANION_CONTROL_TOKEN", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Desktop authorization required")


def fingerprint(config: LLMConfig) -> str:
    return hashlib.sha256(config.model_dump_json().encode("utf-8")).hexdigest()


def public_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LLMAuthenticationError):
        return HTTPException(status_code=401, detail="HTTP 401：API Key 无效")
    if isinstance(exc, LLMHTTPError):
        details = {
            403: "API Key 没有访问权限",
            404: "模型或 API 路径不存在",
            429: "请求过于频繁或配额不足",
        }
        return HTTPException(
            status_code=exc.status_code,
            detail=f"HTTP {exc.status_code}：{details.get(exc.status_code, '模型服务拒绝请求')}",
        )
    if isinstance(exc, LLMTimeoutError):
        return HTTPException(status_code=504, detail="模型请求超时")
    return HTTPException(status_code=502, detail="模型服务连接失败")


@router.get("")
def current(request: Request, x_companion_control_token: str | None = Header(default=None)) -> dict:
    require_control(x_companion_control_token)
    return request.app.state.llm_service.config.public()


@router.post("/test")
async def test_config(
    config: LLMConfig,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
) -> dict:
    require_control(x_companion_control_token)
    started = time.perf_counter()
    try:
        await create_llm_service(config).chat([{"role": "user", "content": "请只回复：OK"}])
    except Exception as exc:
        raise public_error(exc) from exc
    request.app.state.llm_tested = (fingerprint(config), time.monotonic())
    return {
        "ok": True,
        "model": config.model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }


@router.post("/apply")
def apply_config(
    config: LLMConfig,
    request: Request,
    force: bool = False,
    x_companion_control_token: str | None = Header(default=None),
) -> dict:
    require_control(x_companion_control_token)
    tested = getattr(request.app.state, "llm_tested", None)
    if not force and (
        not tested or tested[0] != fingerprint(config) or time.monotonic() - tested[1] > 300
    ):
        raise HTTPException(status_code=409, detail="请先测试连接，或明确选择强制应用")
    # A new snapshot is installed atomically. In-progress conversations retain
    # the old service reference; the next request uses this one.
    request.app.state.llm_service = create_llm_service(config)
    return {"applied": True, **config.public()}


@router.post("/models")
async def list_models(
    config: LLMConfig, x_companion_control_token: str | None = Header(default=None)
) -> dict:
    require_control(x_companion_control_token)
    try:
        models = await create_llm_service(config).provider.list_models()
    except Exception as exc:
        raise public_error(exc) from exc
    return {"models": models}

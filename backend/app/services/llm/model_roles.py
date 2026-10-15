"""按职责选择模型服务，并区分配置能力与实际请求中观察到的能力。

四种职责可以共用服务；确定性本地动作可绕过模型规划。未知能力允许
尝试，空工具选择不是服务缺少 Tool Calling 能力的证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any
from app.core.file_logging import file_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from app.services.llm.service import LLMService

ROLES = ("character_model", "planning_model", "tool_execution_model", "coding_model")
CAPABILITIES = ("text", "vision", "tool_calling", "structured_output", "reasoning", "streaming")


class ExecutionModelUnavailable(ValueError):
    """表示工具请求无可用模型，供上层保留可明确执行的本地动作。"""

    def __init__(self) -> None:
        super().__init__("当前任务需要 Agent 执行模型，但还没有配置可用模型。")


@dataclass
class ModelCapabilities:
    """保留能力来源；仅 observed 值覆盖同名 configured 值。"""

    configured: dict[str, bool | None] = field(default_factory=dict)
    observed: dict[str, bool | None] = field(default_factory=dict)

    def value(self, name: str) -> bool | None:
        """返回观察优先的能力值；未知值为 None，不默认判为不支持。"""
        return self.observed.get(name, self.configured.get(name))

    def public(self) -> dict[str, dict[str, Any]]:
        """供设置界面展示值和来源，不包含 API 密钥。"""
        return {
            key: {
                "value": self.value(key),
                "source": "observed"
                if key in self.observed
                else "configured"
                if self.configured.get(key) is not None
                else "unknown",
            }
            for key in CAPABILITIES
        }


class ModelRoleRouter:
    """根据角色覆盖配置与可观察能力选择当前服务，不创建新模型框架。"""

    def __init__(self, app: FastAPI) -> None:
        self.app, self.overrides, self.capabilities = app, {}, {}
        self.logger = file_logger("app.agent.models", "agent-model-routing.log")

    def configure(self, services: dict[str, LLMService]) -> None:
        """替换职责覆盖并清空能力缓存；当前 app 的默认服务继续可用。"""
        if set(services) - set(ROLES):
            raise ValueError("未知模型职责")
        self.overrides = services
        self.capabilities = {}

    def capability(self, service: LLMService) -> ModelCapabilities:
        """以服务对象身份缓存能力，并合并服务最近观察结果。"""
        identity = id(service)
        if identity not in self.capabilities:
            config = getattr(service, "config", None)
            self.capabilities[identity] = ModelCapabilities(
                dict(getattr(config, "capabilities", {}) or {})
            )
        self.capabilities[identity].observed.update(getattr(service, "observed_capabilities", {}))
        return self.capabilities[identity]

    def service(self, role: str, *, tools: bool = False) -> LLMService:
        """优先指定职责，再按工具请求回退执行服务和角色服务。

        仅显式 tool_calling=false 排除工具候选。未知能力允许请求试探；
        本方法返回首个候选，不在调用失败后依次重新请求所有候选。
        """
        if role not in ROLES:
            raise ValueError("未知模型职责")
        character = self.overrides.get("character_model") or self.app.state.llm_service
        candidates = [self.overrides.get(role)]
        if tools and role != "tool_execution_model":
            candidates.append(self.overrides.get("tool_execution_model"))
        candidates.append(character)
        for candidate in candidates:
            if candidate is None or (
                tools and self.capability(candidate).value("tool_calling") is False
            ):
                continue
            config = getattr(candidate, "config", None)
            self.logger.info(
                json.dumps(
                    {
                        "event": "model_selected",
                        "role": role,
                        "tools": tools,
                        "model": getattr(config, "model", "local-provider"),
                        "tool_calling": self.capability(candidate).value("tool_calling"),
                    }
                )
            )
            return candidate
        self.logger.info(json.dumps({"event": "execution_model_unavailable", "role": role}))
        raise ExecutionModelUnavailable()

    async def plan_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        role: str = "tool_execution_model",
    ) -> dict[str, Any]:
        """请求一次工具规划，实际选中工具后才记录观察到的支持能力。"""
        service = self.service(role, tools=True)
        try:
            result = await service.plan_tools(messages, tools)
        except Exception as error:
            # An empty tool selection is not evidence that the API lacks the capability.
            self.logger.info(
                json.dumps(
                    {"event": "tool_request_failed", "role": role, "error": type(error).__name__}
                )
            )
            raise ExecutionModelUnavailable() from error
        if result.get("tool_calls"):
            self.capability(service).observed["tool_calling"] = True
        else:
            self.logger.info(json.dumps({"event": "no_tool_selection", "role": role}))
        return result

    def public(self) -> dict[str, dict[str, Any]]:
        """展示职责配置的能力，不保证等同每次工具请求的回退选中服务。"""
        return {
            role: {
                "configured": role in self.overrides,
                "capabilities": self.capability(
                    self.overrides.get(role) or self.app.state.llm_service
                ).public(),
            }
            for role in ROLES
        }

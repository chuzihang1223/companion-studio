"""按职责选择模型服务，并区分配置能力与实际请求中观察到的能力。

四种职责可以共用服务；确定性本地动作可绕过模型规划。未知能力允许
尝试，空工具选择不是服务缺少 Tool Calling 能力的证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Any
from app.core.file_logging import file_logger
from app.services.llm.routing.contracts import RoutingError

if TYPE_CHECKING:
    from fastapi import FastAPI
    from app.services.llm.service import LLMService

ROLES = ("character_model", "planning_model", "tool_execution_model", "coding_model")
CAPABILITIES = ("text", "vision", "tool_calling", "structured_output", "reasoning", "streaming")


class ExecutionModelUnavailable(ValueError):
    """表示工具请求无可用模型，供上层保留可明确执行的本地动作。"""

    def __init__(self, routing_error: RoutingError | None = None) -> None:
        self.routing_code = routing_error.public()["code"] if routing_error else None
        super().__init__(
            routing_error.public()["message"]
            if routing_error
            else "当前任务需要 Agent 执行模型，但还没有配置可用模型。"
        )


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
        self.intelligent = None
        self._selection_cache = None
        self._budget_task_id = None
        self._restored_selections = {}
        self.logger = file_logger("app.agent.models", "agent-model-routing.log")

    def configure_routing(self, models, policy, roles):
        """Replace only future task snapshots; never mutate in-flight services."""
        from app.database.session import SessionLocal
        from app.services.llm.routing.budget import BudgetLedger
        from app.services.llm.routing.metrics import ModelMetrics
        from app.services.llm.routing.router import make_snapshot

        if self.intelligent:
            from uuid import uuid4

            refreshed = []
            for record, service in models:
                old = self.intelligent.registry.by_id.get(record.model_id)
                if old and old.service.config == service.config and old.record == record:
                    service = old.service
                elif (
                    old
                    and old.service.config.api_key != service.config.api_key
                    and old.record.config_revision == record.config_revision
                ):
                    record = record.model_copy(update={"config_revision": uuid4().hex})
                refreshed.append((record, service))
            models = refreshed
        metrics = self.intelligent.metrics if self.intelligent else ModelMetrics()
        ledger = self.intelligent.ledger if self.intelligent else BudgetLedger(SessionLocal)
        self.intelligent = make_snapshot(models, policy, roles, metrics, ledger)

    def routing_status(self):
        return (
            self.intelligent.public()
            if self.intelligent
            else {"policy": {"enabled": False}, "models": [], "roles": {}, "recent_requests": []}
        )

    def identity(self):
        if not self.intelligent or not self.intelligent.policy.enabled:
            return None
        from hashlib import sha256

        state = self.intelligent
        value = {
            "policy": state.policy.model_dump(mode="json"),
            "roles": state.roles,
            "models": sorted(
                (entry.record.model_id, entry.version) for entry in state.registry.entries
            ),
        }
        return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def preview(self, role, *, tools=False, requirements=None):
        """Current metadata only: bypass a task cache for scheduler consent checks."""
        if self.intelligent and self.intelligent.policy.enabled:
            return self.intelligent.service(role, tools=tools, requirements=requirements)
        captured = self.capture()
        from app.services.trace.scope import current_trace

        token = current_trace.set(None)
        try:
            return captured.service(role, tools=tools, requirements=requirements)
        finally:
            current_trace.reset(token)

    @staticmethod
    def requirement_key(requirements):
        if requirements is None:
            return None
        value = requirements.model_dump(mode="json")
        value["required"] = sorted(requirements.required)
        value["preferred"] = sorted(requirements.preferred)
        return json.dumps(value, sort_keys=True)

    def selection_snapshot(self):
        return [
            {
                "role": role,
                "tools": tools,
                "requirements": json.loads(requirements) if requirements else None,
                "model_id": service.entry.record.model_id,
                "config_version": service.entry.version,
            }
            for (role, tools, requirements), service in (self._selection_cache or {}).items()
        ]

    def restore_selections(self, rows):
        from app.services.llm.routing.contracts import Requirements, RoutingError

        if len(rows) > 256 or not self.intelligent:
            raise RoutingError("MODEL_SNAPSHOT_INVALID")
        restored = {}
        for row in rows:
            role, tools = row["role"], row["tools"]
            if role not in ROLES or type(tools) is not bool:
                raise RoutingError("MODEL_SNAPSHOT_INVALID")
            entry = self.intelligent.registry.by_id.get(row["model_id"])
            if entry is None or entry.version != row["config_version"]:
                raise RoutingError("MODEL_SNAPSHOT_CHANGED")
            requirements = (
                Requirements.model_validate(row["requirements"]) if row["requirements"] else None
            )
            restored[(role, tools, self.requirement_key(requirements))] = entry.record.model_id
        self._restored_selections = restored

    def capture(self, task_id=None):
        """Capture legacy roles as well as the optional intelligent policy."""
        from types import SimpleNamespace

        captured = object.__new__(ModelRoleRouter)
        captured.app = SimpleNamespace(
            state=SimpleNamespace(llm_service=self.app.state.llm_service)
        )
        captured.overrides = dict(self.overrides)
        captured.capabilities = dict(self.capabilities)
        from uuid import uuid4
        from app.services.trace.scope import current_trace

        trace = current_trace.get()
        captured._budget_task_id = (
            task_id or self._budget_task_id or (trace.task_id if trace else str(uuid4()))
        )
        captured._selection_cache = (
            dict(self._selection_cache or {})
            if task_id is None or task_id == self._budget_task_id
            else {}
        )
        captured._restored_selections = dict(self._restored_selections)
        captured.intelligent, captured.logger = self.intelligent, self.logger
        return captured

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

    def service(self, role: str, *, tools: bool = False, requirements=None) -> LLMService:
        """优先指定职责，再按工具请求回退执行服务和角色服务。

        仅显式 tool_calling=false 排除工具候选。未知能力允许请求试探；
        本方法返回首个候选，不在调用失败后依次重新请求所有候选。
        """
        if role not in ROLES:
            raise ValueError("未知模型职责")
        from app.services.trace.scope import current_trace

        trace = current_trace.get()
        if trace is not None and self._selection_cache is None:
            # A task's first lookup pins the full router, including explicit disable.
            snapshots = getattr(trace, "model_route_snapshots", None)
            if snapshots is None:
                snapshots = trace.model_route_snapshots = {}
            if id(self.app) not in snapshots:
                snapshots[id(self.app)] = self.capture()
            captured = snapshots[id(self.app)]
        else:
            captured = self
        if captured.intelligent and captured.intelligent.policy.enabled:
            key = (role, tools, self.requirement_key(requirements))
            restored = captured._restored_selections.get(key)
            if restored:
                from app.services.llm.routing.contracts import TaskCapabilityResolver
                from app.services.llm.routing.router import TASKS

                requirements = (
                    requirements or TaskCapabilityResolver.resolve(TASKS[role], tools=tools)
                ).model_copy(update={"privacy": "PINNED", "pinned_model_id": restored})
            if captured._selection_cache is not None:
                if key not in captured._selection_cache:
                    captured._selection_cache[key] = captured.intelligent.service(
                        role, tools=tools, requirements=requirements
                    )
                    if captured._selection_cache[key].workflow_id is None:
                        captured._selection_cache[key].task_id = captured._budget_task_id
                return captured._selection_cache[key]
            return captured.intelligent.service(role, tools=tools, requirements=requirements)
        character = captured.overrides.get("character_model") or captured.app.state.llm_service
        candidates = [captured.overrides.get(role)]
        if tools and role != "tool_execution_model":
            candidates.append(captured.overrides.get("tool_execution_model"))
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
        requirements=None,
    ) -> dict[str, Any]:
        """请求一次工具规划，实际选中工具后才记录观察到的支持能力。"""
        try:
            service = self.service(role, tools=True, requirements=requirements)
            result = await service.plan_tools(messages, tools)
        except Exception as error:
            # An empty tool selection is not evidence that the API lacks the capability.
            self.logger.info(
                json.dumps(
                    {"event": "tool_request_failed", "role": role, "error": type(error).__name__}
                )
            )
            raise ExecutionModelUnavailable(
                error if isinstance(error, RoutingError) else None
            ) from error
        if result.get("tool_calls") and not hasattr(service, "entry"):
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

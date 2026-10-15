"""Versioned recovery contracts. Free-form provider exceptions never enter these DTOs."""

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FailureCode(StrEnum):
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    WINDOW_NOT_FOUND = "WINDOW_NOT_FOUND"
    APP_EXITED = "APP_EXITED"
    TARGET_WINDOW_CLOSED = "TARGET_WINDOW_CLOSED"
    PAGE_CHANGED = "PAGE_CHANGED"
    DOM_CHANGED = "DOM_CHANGED"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TOOL_ARGUMENT_INVALID = "TOOL_ARGUMENT_INVALID"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    USER_INTERRUPTION = "USER_INTERRUPTION"
    DESKTOP_LOCKED = "DESKTOP_LOCKED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    FILE_STATE_CHANGED = "FILE_STATE_CHANGED"
    SIDE_EFFECT_UNCERTAIN = "SIDE_EFFECT_UNCERTAIN"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    CHECKPOINT_CONFLICT = "CHECKPOINT_CONFLICT"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    TASK_CANCELLED = "TASK_CANCELLED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


MESSAGES = {
    FailureCode.ELEMENT_NOT_FOUND: "未找到目标控件，需要重新观察。",
    FailureCode.WINDOW_NOT_FOUND: "未找到目标窗口。",
    FailureCode.APP_EXITED: "目标应用已退出，未自动重新启动。",
    FailureCode.TARGET_WINDOW_CLOSED: "目标窗口已关闭。",
    FailureCode.PAGE_CHANGED: "当前网页与目标不一致。",
    FailureCode.DOM_CHANGED: "网页结构已变化，需要重新观察。",
    FailureCode.NETWORK_TIMEOUT: "网络请求超时，完成状态尚需核验。",
    FailureCode.TOOL_TIMEOUT: "工具等待超时，完成状态尚需核验。",
    FailureCode.MODEL_TIMEOUT: "模型请求超时，未自动重复付费请求。",
    FailureCode.MODEL_UNAVAILABLE: "模型服务不可用。",
    FailureCode.TOOL_ARGUMENT_INVALID: "工具参数或资源路径无效。",
    FailureCode.PERMISSION_DENIED: "没有有效授权，未执行工具。",
    FailureCode.USER_INTERRUPTION: "检测到用户干预，自动操作已停止。",
    FailureCode.DESKTOP_LOCKED: "Windows 已锁屏，等待用户接管。",
    FailureCode.VERIFICATION_FAILED: "执行结果尚未通过验证。",
    FailureCode.RESOURCE_CONFLICT: "资源正由其他操作占用。",
    FailureCode.FILE_STATE_CHANGED: "文件状态发生变化，已阻止自动覆盖。",
    FailureCode.SIDE_EFFECT_UNCERTAIN: "操作可能已经生效，禁止直接重放。",
    FailureCode.CHECKPOINT_INVALID: "检查点损坏或版本不受支持。",
    FailureCode.CHECKPOINT_CONFLICT: "检查点与当前任务或工作区不一致。",
    FailureCode.MAX_RETRIES_EXCEEDED: "恢复预算耗尽，任务已安全停止。",
    FailureCode.TASK_CANCELLED: "任务已取消。",
    FailureCode.UNKNOWN_ERROR: "发生未分类错误，需要人工检查。",
}


class RecoveryState(StrEnum):
    INTERRUPTED = "INTERRUPTED"
    RECOVERY_AVAILABLE = "RECOVERY_AVAILABLE"
    REVALIDATING = "REVALIDATING"
    WAITING_USER = "WAITING_USER"
    RESUMING = "RESUMING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNRECOVERABLE = "UNRECOVERABLE"
    NEEDS_REVIEW = "NEEDS_REVIEW"


TERMINAL = {"COMPLETED", "CANCELLED", "UNRECOVERABLE"}
TRANSITIONS = {
    "INTERRUPTED": {"REVALIDATING", "CANCELLED", "NEEDS_REVIEW"},
    "RECOVERY_AVAILABLE": {"REVALIDATING", "RESUMING", "CANCELLED", "WAITING_USER"},
    "REVALIDATING": {"RECOVERY_AVAILABLE", "NEEDS_REVIEW", "UNRECOVERABLE", "CANCELLED"},
    "WAITING_USER": {"REVALIDATING", "CANCELLED", "NEEDS_REVIEW", "RECOVERING"},
    "RESUMING": {"RECOVERING", "COMPLETED", "FAILED", "INTERRUPTED", "CANCELLED", "NEEDS_REVIEW"},
    "RECOVERING": {
        "COMPLETED",
        "FAILED",
        "WAITING_USER",
        "INTERRUPTED",
        "CANCELLED",
        "NEEDS_REVIEW",
    },
    "FAILED": {"REVALIDATING", "CANCELLED", "NEEDS_REVIEW"},
    "NEEDS_REVIEW": {"REVALIDATING", "CANCELLED", "UNRECOVERABLE"},
}


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_code: FailureCode
    category: str
    message: str
    source: str
    tool_name: str
    task_id: UUID
    step_id: UUID
    trace_id: UUID | None = None
    severity: str = "warning"
    retryable: bool = False
    requires_reobservation: bool = True
    requires_user_approval: bool = True
    evidence_refs: list[UUID] = Field(default_factory=list, max_length=8)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RecoveryBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_retry_per_step: int = Field(default=2, ge=0, le=2)
    max_recovery_cycles: int = Field(default=4, ge=0, le=8)
    max_total_steps: int = Field(default=12, ge=1, le=24)
    max_task_duration: int = Field(default=1800, ge=1, le=1800)
    # This deterministic planner does not spend model/API quota. A future model
    # fallback must obtain consent and enforce usage before raising these limits.
    max_model_calls: int = Field(default=0, ge=0, le=0)
    max_estimated_cost: float = Field(default=0, ge=0, le=0)


class RecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    level: int = Field(ge=0, le=3)
    strategy: str
    retry: bool = False
    observe: bool = False
    approval: bool = False
    stop: bool = False

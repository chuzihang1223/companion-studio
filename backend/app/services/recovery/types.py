"""Versioned recovery contracts. Free-form provider exceptions never enter these DTOs."""

from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FailureCode(StrEnum):
    SCREEN_CAPTURE_FAILED = "SCREEN_CAPTURE_FAILED"
    UIA_ELEMENT_STALE = "UIA_ELEMENT_STALE"
    ELEMENT_AMBIGUOUS = "ELEMENT_AMBIGUOUS"
    WINDOW_IDENTITY_CHANGED = "WINDOW_IDENTITY_CHANGED"
    FOCUS_LOST = "FOCUS_LOST"
    DPI_MAPPING_INVALID = "DPI_MAPPING_INVALID"
    DISPLAY_CHANGED = "DISPLAY_CHANGED"
    VISION_MODEL_UNAVAILABLE = "VISION_MODEL_UNAVAILABLE"
    ACTION_STATE_UNKNOWN = "ACTION_STATE_UNKNOWN"
    POST_ACTION_VERIFICATION_FAILED = "POST_ACTION_VERIFICATION_FAILED"
    MCP_SERVER_UNAVAILABLE = "MCP_SERVER_UNAVAILABLE"
    MCP_CONNECTION_TIMEOUT = "MCP_CONNECTION_TIMEOUT"
    MCP_AUTH_REQUIRED = "MCP_AUTH_REQUIRED"
    MCP_PROTOCOL_MISMATCH = "MCP_PROTOCOL_MISMATCH"
    MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
    MCP_SCHEMA_CHANGED = "MCP_SCHEMA_CHANGED"
    MCP_TOOL_TIMEOUT = "MCP_TOOL_TIMEOUT"
    MCP_RESULT_INVALID = "MCP_RESULT_INVALID"
    MCP_PERMISSION_DENIED = "MCP_PERMISSION_DENIED"
    MCP_SIDE_EFFECT_UNKNOWN = "MCP_SIDE_EFFECT_UNKNOWN"
    MCP_ARGUMENT_INVALID = "MCP_ARGUMENT_INVALID"
    SKILL_INVALID = "SKILL_INVALID"
    SKILL_DEPENDENCY_MISSING = "SKILL_DEPENDENCY_MISSING"
    SKILL_PERMISSION_DENIED = "SKILL_PERMISSION_DENIED"
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
    FailureCode.SCREEN_CAPTURE_FAILED: "窗口截图失败，未复制其他桌面内容。",
    FailureCode.UIA_ELEMENT_STALE: "控件已变化，请重新观察定位。",
    FailureCode.ELEMENT_AMBIGUOUS: "存在多个或不确定候选，未自动点击。",
    FailureCode.WINDOW_IDENTITY_CHANGED: "窗口身份或位置已变化，操作已停止。",
    FailureCode.FOCUS_LOST: "目标窗口失去焦点，未继续输入。",
    FailureCode.DPI_MAPPING_INVALID: "坐标映射发生变化，未点击旧位置。",
    FailureCode.DISPLAY_CHANGED: "显示器布局已变化，需要重新观察。",
    FailureCode.VISION_MODEL_UNAVAILABLE: "未配置已授权的视觉模型，本地 UIA/OCR 仍可用。",
    FailureCode.ACTION_STATE_UNKNOWN: "操作结果未知，禁止自动重复。",
    FailureCode.POST_ACTION_VERIFICATION_FAILED: "动作可能已发生，但预期结果未通过验证。",
    FailureCode.MCP_SERVER_UNAVAILABLE: "MCP 服务器不可用；其他工具可继续使用。",
    FailureCode.MCP_CONNECTION_TIMEOUT: "MCP 连接超时，未重放工具调用。",
    FailureCode.MCP_AUTH_REQUIRED: "MCP 服务器需要重新授权。",
    FailureCode.MCP_PROTOCOL_MISMATCH: "MCP 协议版本或消息格式不兼容。",
    FailureCode.MCP_TOOL_NOT_FOUND: "MCP 工具已移除或未连接。",
    FailureCode.MCP_SCHEMA_CHANGED: "MCP 工具定义已变化，请刷新并重新审批。",
    FailureCode.MCP_TOOL_TIMEOUT: "MCP 工具等待超时，完成状态需核验。",
    FailureCode.MCP_RESULT_INVALID: "MCP 返回无效或超过限额。",
    FailureCode.MCP_PERMISSION_DENIED: "当前没有 MCP 工具授权。",
    FailureCode.MCP_SIDE_EFFECT_UNKNOWN: "外部操作可能已生效，禁止自动重放。",
    FailureCode.MCP_ARGUMENT_INVALID: "MCP 参数未通过本地 Schema 校验。",
    FailureCode.SKILL_INVALID: "Skill 格式无效或内容已变化。",
    FailureCode.SKILL_DEPENDENCY_MISSING: "Skill 所需工具或能力不可用。",
    FailureCode.SKILL_PERMISSION_DENIED: "Skill 未获授权。",
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
    "REVALIDATING": {
        "RECOVERY_AVAILABLE",
        "NEEDS_REVIEW",
        "UNRECOVERABLE",
        "CANCELLED",
        "INTERRUPTED",
    },
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

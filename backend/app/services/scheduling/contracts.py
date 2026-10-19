"""Fixed, bounded scheduler protocol. Stored definitions never contain executable source."""

from datetime import datetime, time
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from app.services.workflows.contracts import Strict, bounded
from app.services.scheduling.time_rules import wall_instant, zone_name, utc

TERMINAL = {"COMPLETED", "FAILED", "SKIPPED", "CANCELLED", "INTERRUPTED", "NEEDS_REVIEW", "MISSED"}
ACTIVE = {"CLAIMED", "RUNNING", "WAITING_PERMISSION", "WAITING_ENVIRONMENT"}
PENDING = {"PENDING", "DUE", "QUEUED"}


class ScheduleError(ValueError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class Trigger(Strict):
    at: str | None = Field(default=None, max_length=30)
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}(?::\d{2})?$")
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    interval_seconds: int = Field(default=300, ge=300, le=2592000)
    start_at: str | None = Field(default=None, max_length=50)
    condition_type: Literal["FILE_CHANGE", "WORKFLOW_STATE"] | None = None
    source_id: str | None = Field(default=None, max_length=36)
    scope: Literal["selected_source"] = "selected_source"
    operator: Literal["changed", "equals"] = "changed"
    expected_value: Literal["COMPLETED", "FAILED"] = "COMPLETED"
    poll_interval: int = Field(default=30, ge=5, le=3600)
    debounce: int = Field(default=5, ge=1, le=300)
    cooldown: int = Field(default=60, ge=5, le=86400)

    @model_validator(mode="after")
    def checked(self):
        if self.at:
            if datetime.fromisoformat(self.at).tzinfo is not None:
                raise ValueError("SCHEDULE_LOCAL_TIME_REQUIRED")
        if self.time:
            time.fromisoformat(self.time)
        if len(set(self.weekdays)) != len(self.weekdays) or any(
            day not in range(7) for day in self.weekdays
        ):
            raise ValueError("SCHEDULE_WEEKDAY_INVALID")
        if self.start_at:
            utc(self.start_at)
        if self.source_id:
            UUID(self.source_id)
        return self


class ScheduleDefinition(Strict):
    schedule_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    conversation_id: str
    workflow_id: str
    workflow_version_id: str
    inputs: dict = Field(default_factory=dict, max_length=30)
    trigger_type: Literal["ONCE", "INTERVAL", "DAILY", "WEEKLY", "CONDITION"] = "ONCE"
    trigger_config: Trigger
    timezone: str = Field(default="Asia/Shanghai", max_length=80)
    enabled: bool = True
    permission_policy: Literal["CURRENT_RUNTIME"] = "CURRENT_RUNTIME"
    misfire_policy: Literal["SKIP_MISSED", "RUN_ONCE_AFTER_RESUME", "REQUIRE_REVIEW"] = (
        "SKIP_MISSED"
    )
    concurrency_policy: Literal["SKIP_IF_RUNNING", "QUEUE_ONE", "REJECT_OVERLAP"] = (
        "SKIP_IF_RUNNING"
    )
    notification: bool = True
    expiry_window: int = Field(default=3600, ge=60, le=86400)
    catch_up_limit: Literal[1] = 1
    max_backlog: int = Field(default=1, ge=1, le=5)
    max_consecutive_runs: int = Field(default=100, ge=1, le=1000)
    execution_timeout: int = Field(default=600, ge=5, le=1800)

    @field_validator("schedule_id", "conversation_id", "workflow_id", "workflow_version_id")
    @classmethod
    def identifiers(cls, value):
        return str(UUID(value))

    @field_validator("timezone")
    @classmethod
    def timezones(cls, value):
        return zone_name(value)

    @model_validator(mode="after")
    def compatible(self):
        bounded(self.model_dump(mode="json"), limit=64000)
        config = self.trigger_config
        if self.trigger_type == "ONCE" and (
            not config.at or wall_instant(datetime.fromisoformat(config.at), self.timezone) is None
        ):
            raise ValueError("SCHEDULE_ONCE_TIME_INVALID")
        if self.trigger_type in {"DAILY", "WEEKLY"} and not config.time:
            raise ValueError("SCHEDULE_TIME_REQUIRED")
        if self.trigger_type == "WEEKLY" and not config.weekdays:
            raise ValueError("SCHEDULE_WEEKDAY_REQUIRED")
        if self.trigger_type == "CONDITION" and (not config.condition_type or not config.source_id):
            raise ValueError("SCHEDULE_SOURCE_REQUIRED")
        return self

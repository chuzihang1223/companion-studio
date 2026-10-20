"""评测输入只描述内置任务，不接受脚本、绝对目录或用户自带执行器。"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Mode(StrEnum):
    MOCK = "mock"
    SANDBOX = "sandbox"
    WINDOWS = "windows_gui"
    LIVE = "live_model"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkCase(StrictModel):
    case_id: str = Field(pattern=r"^(EVAL|REC|MCP|WF|KB|CU|SCH|VOICE)-\d{3}$")
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    category: str = Field(
        pattern=r"^(routing|files|browser|desktop|control|recovery|extensions|workflow|knowledge|computer|scheduler|voice)$"
    )
    difficulty: str = Field(pattern=r"^(basic|intermediate)$")
    input: str = Field(min_length=1, max_length=1000)
    environment: str = Field(
        pattern=r"^(studio|recovery|mcp|workflow|knowledge|computer|scheduler|voice)-fixture-v1$"
    )
    execution_mode: list[Mode] = Field(min_length=1, max_length=4)
    expected_outcome: dict[str, str | bool | int] = Field(max_length=15)
    grader: str = Field(
        pattern=r"^(exact|structured|file|exit_code|browser_dom|desktop|permission|trace|recovery|extensions|workflow|knowledge|computer|scheduler|voice)$"
    )
    allowed_tools: list[str] = Field(max_length=8)
    allowed_paths: list[str] = Field(max_length=8)
    timeout_seconds: int = Field(default=15, ge=2, le=60)
    max_steps: int = Field(default=4, ge=1, le=8)
    max_retries: int = Field(default=2, ge=0, le=2)
    tags: list[str] = Field(max_length=8)
    dataset_version: str = Field(pattern=r"^1\.0\.0$")

    @field_validator("allowed_paths")
    @classmethod
    def relative_paths(cls, values):
        for value in values:
            path = PurePosixPath(value)
            if (
                not value
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in value
                or ":" in value
            ):
                raise ValueError("用例目录必须是隔离工作区内的相对路径")
        return values

    @field_validator("allowed_tools", "tags")
    @classmethod
    def identifiers(cls, values):
        if any(not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", value) for value in values):
            raise ValueError("无效的工具或标签标识")
        return values


class RunRequest(StrictModel):
    dataset_id: str = Field(
        default="studio-core",
        pattern=r"^(studio-core|recovery-core|mcp-skills-core|workflow-core|knowledge-core|computer-use-core|scheduler-core|voice-core)$",
    )
    case_ids: list[str] = Field(default_factory=list, max_length=36)
    knowledge_mode: Literal["KeywordOnly", "HybridLocal", "HybridRemote", "MockRAG"] = "KeywordOnly"
    visual_consent: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    mode: Mode = Mode.MOCK
    model_role: str = Field(
        default="planning_model",
        pattern=r"^(character_model|planning_model|tool_execution_model|coding_model)$",
    )
    repetitions: int = Field(default=1, ge=1, le=5)
    concurrency: int = Field(default=1, ge=1, le=2)
    timeout_seconds: int = Field(default=20, ge=2, le=60)
    max_duration_seconds: int = Field(default=600, ge=5, le=1800)
    max_requests: int = Field(default=0, ge=0, le=100)
    token_budget: int = Field(default=0, ge=0, le=200000)
    live_consent: bool = False

    @field_validator("case_ids")
    @classmethod
    def unique_ids(cls, values):
        if len(values) != len(set(values)) or any(
            not re.fullmatch(r"(EVAL|REC|MCP|WF|KB|CU|SCH|VOICE)-\d{3}", v) for v in values
        ):
            raise ValueError("测试项编号必须唯一且有效")
        return values

    @model_validator(mode="after")
    def budgets(self):
        if (
            self.dataset_id
            in {
                "recovery-core",
                "mcp-skills-core",
                "workflow-core",
                "knowledge-core",
                "computer-use-core",
                "scheduler-core",
                "voice-core",
            }
            and self.mode == Mode.LIVE
        ):
            raise ValueError("Recovery 故障集仅使用确定性模型，不启动真实模型请求")
        if self.mode == Mode.LIVE:
            if not self.live_consent or self.max_requests < 1 or self.token_budget < 1:
                raise ValueError("真实模型评测须明确授权并设置请求和 Token 上限")
            if self.concurrency != 1:
                raise ValueError("真实模型评测采用串行请求预算")
        elif self.live_consent or self.max_requests or self.token_budget:
            raise ValueError("非真实模型模式不得配置外部调用预算")
        if self.mode == Mode.WINDOWS and self.concurrency != 1:
            raise ValueError("Windows GUI 评测必须串行")
        return self


class RunAction(StrictModel):
    action: str = Field(pattern=r"^(pause|resume|cancel)$")

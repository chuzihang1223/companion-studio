"""按计划执行工具，将实际观察、验证和有界恢复发布到工作台。

当前模块协调步骤和桌面桥，不让模型直接操作窗口。暂停/取消检查点、
工具超时和目标验证各有职责，正常返回不等同用户所有子目标均被覆盖。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from typing import TYPE_CHECKING, Any
from app.services.desktop.lifecycle import TaskHandle, TaskCancelled
from app.services.desktop.preferences import read_preferences
from app.services.trusted_actions import trusted_actions, action_for, risk_for
from app.core.file_logging import file_logger
from app.services.desktop.goal_verifier import GoalVerifier
from app.services.desktop.music_task import music_context
from app.services.trace.scope import span_scope
from app.services.trace.privacy import arguments as trace_arguments
from app.services.trace.scope import current_trace
from app.services.recovery.checkpoints import RecoveryError
from app.services.recovery.execution import RecoveryExecution
from app.services.recovery.events import emit as recovery_event
from app.services.recovery.step_runner import run_step
from app.services.recovery.policy import READ_ONLY

if TYPE_CHECKING:
    from fastapi import FastAPI
    from app.services.agent_service import AgentContext
    from app.services.agent_tools import ToolCall, ToolDetail, ToolResult

LABELS = {
    "app.launch": "启动应用",
    "app.detect": "检测应用",
    "app.status": "观察应用",
    "app.focus": "聚焦应用",
    "music.play": "搜索并播放歌曲",
    "music.search": "搜索歌曲",
    "coding.task": "修改项目并验证测试",
    "system.get_status": "读取系统状态",
    "browser.search_site": "网页搜索",
    "browser.open_url": "打开网页",
    "code.read_file": "读取文件",
    "code.edit_file": "修改文件",
    "code.create_file": "创建文件",
    "code.run": "运行测试",
}


class VerificationFailure(RuntimeError):
    """携带原始失败观察，便于重试结束时保留可展示错误。"""

    def __init__(self, result: ToolResult) -> None:
        super().__init__(result.get("error") or "此步骤未通过验证")
        self.result = result


class AgentTaskRuntime:
    """持有当前 Work Session 的 Handle，协调工具步骤与结果检查。"""

    def __init__(self, app: FastAPI) -> None:
        self.app = app
        self.handles: dict[str, TaskHandle] = {}
        self.executions: dict[str, RecoveryExecution] = {}
        self.logger = file_logger("app.runtime", "agent-runtime.log")

    def prepare(self, context: AgentContext) -> None:
        """从工具生成可见步骤并准备 Handle；此阶段不执行任何工具。"""
        context.knowledge_service = getattr(self.app.state, "knowledge", None)
        titles = []
        for call in context.calls:
            if (
                call["name"] in {"music.play", "music.search"}
                and call["arguments"].get("provider") != "local"
            ):
                titles += ["检测音乐客户端", "启动并等待窗口", "定位搜索框并搜索歌曲"]
                if call["name"] == "music.play":
                    titles += ["选择匹配歌曲", "验证歌名与播放状态"]
            else:
                titles.append(LABELS.get(call["name"], call["name"]))
        titles.append("验证目标并反馈")
        self.app.state.work_state.set_steps(context.work_session_id, titles)
        self.handles.setdefault(context.work_session_id, TaskHandle())

    def control(self, session_id: str, action: str) -> None:
        """变更暂停/接管/取消状态，取消时同时唤醒注册器中的任务。"""
        handle = self.handles.get(session_id)
        if handle:
            handle.control(action)
        recovery = self.executions.get(session_id)
        if recovery and recovery.manager:
            recovery.manager.state(
                recovery.root,
                "WAITING_USER"
                if action in {"pause", "takeover"}
                else "RECOVERING"
                if action == "resume"
                else "CANCELLED",
            )
        if action in {"cancel", "stop"} and getattr(self.app.state, "runtime_registry", None):
            self.app.state.runtime_registry.cancel_session(session_id)
        status = {"pause": "paused", "takeover": "waiting_user", "resume": "observing"}.get(
            action, "cancelled"
        )
        self.app.state.work_state.emit(session_id, "task_" + action, {}, status=status)

    async def run(
        self, context: AgentContext, approved_ids: set[str]
    ) -> tuple[list[dict[str, Any]], list[ToolDetail], bool]:
        """逐个检查权限和执行工具，返回模型观察、工具明细及目标判定。

        每次操作前过检查点，拒绝权限会形成失败结果而不进入执行器。
        后续事件/持久回复由 AgentService 处理，运行配额由注册器清理。
        """
        session = context.work_session_id
        work = self.app.state.work_state
        handle = self.handles.setdefault(session, TaskHandle())
        trusted_actions.bind_extension_task(session, context.conversation_id)
        observations, details = [], []
        handle.position = 0
        manager = getattr(self.app.state, "checkpoint_manager", None)
        if manager is None and getattr(self.app.state, "recovery_required", False):
            raise RecoveryError("CHECKPOINT_STORAGE_UNAVAILABLE", 503)
        trace = current_trace.get()
        root = (
            await asyncio.to_thread(manager.begin, context, work, trace.trace_id if trace else None)
            if manager
            else context.task_id
        )
        recovery = RecoveryExecution(self.app, context, manager, root)
        recovery.approved_ids = approved_ids
        self.executions[session] = recovery
        try:
            for index, call in enumerate(context.calls):
                await handle.checkpoint()
                if context.selected_skills:
                    self.app.state.agent_skills.validate_selection(context.selected_skills)
                if work.is_stopped(session):
                    raise TaskCancelled("任务已停止")
                with span_scope(
                    "tool",
                    "TOOL_STARTED",
                    "TOOL_COMPLETED",
                    tool_name=call["name"],
                    metadata={
                        "requires_permission": call.get("requires_confirmation", False),
                        "executed": False,
                        "side_effect": call["name"] not in READ_ONLY
                        and call.get("side_effect_class") != "READ_ONLY",
                        "tool_call_id": call["id"],
                        "parameter_summary": trace_arguments(call["arguments"]),
                    },
                ) as span:
                    action = action_for(call, context.executor.workspace)
                    allowed = trusted_actions.permitted(action, call["id"], approved_ids, session)
                    if context.resume_verified and call["id"] in context.resume_verified:
                        recovery.call = call
                        if manager:
                            await asyncio.to_thread(manager.reuse, root, context, call)
                        work.step_status(session, handle.position, "done")
                        handle.position += 1
                        result = {
                            "verified": True,
                            "reconciled": True,
                            "summary": "已重新核验检查点，未重复执行工具。",
                        }
                        recovery_event("CHECKPOINT_VALIDATED", evidence={"verified": True})
                    else:
                        prepared = False
                        try:
                            await recovery.before(call, index, allowed)
                            prepared = True
                        except (ValueError, OSError) as error:
                            failure = recovery.classify(error, name=call["name"])
                            result = {
                                "error": failure.message,
                                "code": failure.error_code.value,
                                "verified": False,
                            }
                        if not prepared:
                            pass  # Invalid resource: no EXECUTING intent, therefore no tool.
                        elif not allowed:
                            result = {
                                "error": "未得到用户授权，未执行",
                                "verified": False,
                                "code": "PERMISSION_DENIED",
                            }
                            recovery.classify(result=result)
                        else:
                            result = await self.execute_call(context, call, handle)
                            await recovery.after(result)
                    span.result(result)
                    if not allowed:
                        span.code = "PERMISSION_DENIED"
                    status = (
                        "failed"
                        if result.get("error") or result.get("verified") is False
                        else "done"
                    )
                    work.emit(
                        session,
                        "tool_completed",
                        {
                            "name": call["name"],
                            "status": status,
                            "result": result,
                            "summary": result.get("public_error")
                            or result.get("summary")
                            or LABELS.get(
                                call["name"], "未完成" if status == "failed" else "已执行"
                            ),
                        },
                    )
                    details.append({"name": call["name"], "status": status, "result": result})
                    observations.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    if status == "failed":
                        break  # A failed prerequisite cannot authorize later planned effects.
            verified = GoalVerifier().verify(context.user_text, details)
            work.emit(
                session,
                "goal_verification",
                {
                    "verified": verified,
                    "summary": "目标验证通过" if verified else "目标尚未通过验证",
                },
                status="verifying",
            )
            if manager and not verified:
                await asyncio.to_thread(
                    manager.state,
                    root,
                    "NEEDS_REVIEW" if recovery.blocked else "FAILED",
                    recovery.failure,
                )
            return observations, details, verified
        finally:
            self.executions.pop(session, None)
            if handle.cancelled:
                self.handles.pop(session, None)

    async def step(
        self,
        context: AgentContext,
        title: str,
        operation: Callable[[], Awaitable[ToolResult]],
        handle: TaskHandle,
        max_attempts: int = 3,
        music_identity: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> ToolResult:
        """Classify failures and recover only with evidence, budget and fresh grants."""
        recovery = self.executions.get(context.work_session_id)

        def permitted():
            if not recovery or not recovery.call:
                return True
            call = recovery.call
            return trusted_actions.permitted(
                action_for(call, context.executor.workspace),
                call["id"],
                recovery.approved_ids,
                context.work_session_id,
            )

        return await run_step(
            self,
            context,
            title,
            operation,
            handle,
            min(max_attempts, context.retry_max_attempts)
            if getattr(context, "retry_max_attempts", None) is not None
            else max_attempts,
            music_identity,
            operation_name,
            permitted,
        )

    async def execute_call(
        self, context: AgentContext, call: ToolCall, handle: TaskHandle
    ) -> ToolResult:
        """按工具类别分发，并将实际结果放入对应工作台模块。

        桌面工具通过 Electron 桥回传观察；文件工具在线程中执行。
        code.run 传递取消/暂停回调，但协程取消不能强制停止所有线程。
        """
        name, args = call["name"], call["arguments"]
        session, work = context.work_session_id, self.app.state.work_state
        if name.startswith("knowledge."):
            from app.services.knowledge.runtime import execute
            from app.services.knowledge.contracts import KnowledgeError

            try:
                return await self.step(
                    context, "知识库检索", lambda: execute(self.app, context, call), handle
                )
            except KnowledgeError as error:
                return {"verified": False, "error": error.code, "code": error.code}
        if context.knowledge_tainted and name.startswith(("mcp.", "browser.")):
            return {
                "verified": False,
                "error": "KNOWLEDGE_EXPORT_NOT_GRANTED",
                "code": "KNOWLEDGE_EXPORT_NOT_GRANTED",
            }
        if name == "skill.read_reference":
            if args["skill_id"] not in {
                s["skill_id"] for s in context.selected_skills or []
            } or not args["path"].startswith("references/"):
                return {
                    "verified": False,
                    "error": "SKILL_PERMISSION_DENIED",
                    "code": "SKILL_PERMISSION_DENIED",
                }

            async def read_reference():
                text = self.app.state.agent_skills.read_file(args["skill_id"], args["path"])
                return {"verified": True, "content": text, "trust": "untrusted_skill_reference"}

            return await self.step(context, "读取 Skill 参考", read_reference, handle)
        if name.startswith("mcp."):
            adapter = getattr(self.app.state, "mcp_adapter", None)
            if adapter is None:
                return {
                    "error": "MCP_SERVER_UNAVAILABLE",
                    "code": "MCP_SERVER_UNAVAILABLE",
                    "verified": False,
                }
            work.emit(
                session,
                "tool_started",
                {"name": name, "server_id": call.get("server_id"), "summary": "调用外部 MCP 工具"},
            )
            return await self.step(context, "MCP 工具调用", lambda: adapter.execute(call), handle)
        desktop = self.app.state.desktop_bridge
        if name.startswith(("app.", "media.", "music.", "browser.")) or name == "system.get_status":
            if not read_preferences()["automation_enabled"]:
                return {"error": "桌面自动操作已关闭，未控制软件", "verified": False}
        music_bound = name in {"music.play", "music.search"} and args.get("provider") != "local"
        identity = (
            music_context(
                context, args.get("provider") or read_preferences()["music_player"], args["query"]
            )
            if music_bound
            else None
        )
        work.emit(
            session,
            "tool_started",
            {
                "name": name,
                "summary": LABELS.get(name, name),
                **({"music_context": identity} if identity else {}),
            },
        )
        if name in {"music.play", "music.search"} and args.get("provider") != "local":
            # 音乐每阶段绑定同一任务/应用上下文，避免跨会话误用搜索结果；
            # 仅在适配器真实验证播放后才形成已播放反馈。
            app = args.get("provider") or read_preferences()["music_player"]
            target = {"app": app, "query": args["query"], "music_context": identity}
            stages = [
                ("检测音乐客户端", "app.detect"),
                ("启动并等待窗口", "app.launch"),
                ("定位搜索框并搜索歌曲", "music.search"),
            ]
            if name == "music.play":
                stages += [
                    ("选择匹配歌曲", "music.select_play"),
                    ("验证歌名与播放状态", "music.verify"),
                ]
            result = {}
            for title, operation in stages:
                identity = {**identity, "currentStep": operation}
                target = {**target, "music_context": identity}
                # The adapter owns its bounded page recovery. Do not repeat the
                # same entire search/click step through generic runtime retries.
                result = await self.step(
                    context,
                    title,
                    lambda op=operation: desktop.request(op, target, session),
                    handle,
                    max_attempts=1,
                    music_identity=identity,
                )
                identity = result.get("music_context") or identity
                result = {**result, "music_context": identity}
                if result.get("error"):
                    return result
                if operation == "app.detect" and not result.get("installed"):
                    return {
                        "error": "未安装所选音乐客户端",
                        "verified": False,
                        "music_context": identity,
                        "app": app,
                        "provider": app,
                        "application_id": app,
                    }
                if operation in {"app.launch", "music.search"}:
                    work.module(session, "app", "音乐客户端", {"app": app, **result})
                if operation in {"music.search", "music.verify"}:
                    work.module(session, "media", "当前媒体", {"app": app, **result})
            return {
                **result,
                "summary": "已验证歌曲播放" if name == "music.play" else "已读取歌曲搜索结果",
                "action": "desktop_completed",
            }
        if name.startswith("app.") or name.startswith("media.") or name == "system.get_status":
            operation = "system.status" if name == "system.get_status" else name
            if call.get("target_text"):
                args = {**args, "target_text": call["target_text"]}
            result = await self.step(
                context,
                LABELS.get(name, "媒体操作"),
                lambda: desktop.request(operation, args, session),
                handle,
            )
            kind = (
                "system"
                if name.startswith("system.")
                else "media"
                if name.startswith("media.")
                else "app"
            )
            work.module(session, kind, "系统状态" if kind == "system" else "应用状态", result)
            return result
        if name.startswith("browser."):
            result = await self.step(
                context, "打开并读取网页", lambda: desktop.request(name, args, session), handle
            )
            work.module(session, "web", str(result.get("title") or "网页"), result)
            return result
        if name == "coding.task":
            from app.services.coding.service import CodingAgentService

            return await self.step(
                context,
                "代码智能体执行与验证",
                lambda: CodingAgentService(self.app, context, handle).run(args["goal"]),
                handle,
            )
        if name == "code.run":
            runner = getattr(context.executor, "run_script", None)
            operation = runner or context.executor._run_python
            target = args["path"] if runner else context.executor._path(args["path"])
            result = await self.step(
                context,
                "运行项目脚本",
                lambda: asyncio.to_thread(
                    operation,
                    target,
                    lambda output: work.module(
                        session, "terminal", "项目脚本", {"output": output, "status": "executing"}
                    ),
                    lambda: handle.cancelled,
                    lambda: handle.paused,
                ),
                handle,
            )
            work.module(session, "terminal", "项目脚本", result)
            if result.get("exit_code") != 0:
                result.update(error="脚本执行未通过", verified=False)
            return result
        result = await self.step(
            context,
            LABELS.get(name, name),
            lambda: asyncio.to_thread(context.executor.execute, call),
            handle,
        )
        if result.get("path"):
            work.module(
                session,
                "file",
                str(args.get("path") or "任务文件"),
                {"path": args.get("path"), **result},
            )
        if result.get("diff"):
            work.module(
                session,
                "code",
                str(args.get("path") or "代码"),
                {"path": args.get("path"), **result},
            )
        if (
            context.executor
            and name.startswith(("workspace.", "code.", "filesystem."))
            and getattr(context.executor, "project", context.executor.workspace)
        ):
            work.module(session, "files", "项目文件", {"root": str(context.executor.workspace)})
        if "output" in result:
            work.module(session, "terminal", str(args.get("path") or "终端"), result)
            if result.get("exit_code") != 0:
                result.update(error="测试执行失败", verified=False)
        return result

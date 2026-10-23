import asyncio
from app.services.coding.provider import CodingAgentProvider
from app.services.coding.codex import CodexProvider
from app.services.desktop.preferences import read_preferences
from app.services.agent_tools import ToolRegistry


class InternalCodingProvider(CodingAgentProvider):
    def __init__(self, app, context, handle, publish):
        super().__init__()
        self.app, self.context, self.handle = app, context, handle

        def recorded(kind, title, data):
            self.events.append({"kind": kind, "title": title, "data": data})
            publish(kind, title, data)

        self.publish = recorded

    async def cancel_task(self):
        self.handle.control("cancel")

    async def create_task(self, goal):
        self.status = "executing"
        executor = self.context.executor
        registry = ToolRegistry()
        tools = [
            t
            for t in registry.schemas()
            if t["function"]["name"]
            in {
                "filesystem_list",
                "code_read_file",
                "code_edit_file",
                "code_create_file",
                "code_run",
            }
        ]
        mode = getattr(self.context, "coding_mode", "virtual_only")
        apply = mode == "write_workspace"
        requested_tests = any(
            word in self.context.user_text.lower()
            for word in ("运行", "测试", "跑一下", "test", "run ")
        )
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是项目编码执行器。用户明确要求修改项目，按现有权限策略读取、编辑与验证。"
                    if apply
                    else "你是草稿编码执行器。读取可以来自项目，创建/编辑只更新虚拟草稿，绝不写回项目。"
                )
                + "当前执行模式："
                + mode
                + "，模型不能改变模式。先读取相关文件或已有草稿，再生成代码/建议。"
                + (
                    "用户要求运行测试，可在独立 sandbox 验证草稿。"
                    if requested_tests
                    else "本轮未要求运行代码，不调用 code_run。"
                )
                + "只用相对路径，不接触密钥、应用数据或其他目录。测试脚本可用 unittest 并输出真实结果。"
                "不要安装依赖。最多6轮工具调用。",
            },
            {"role": "user", "content": goal + "\n项目目录：" + str(executor.workspace)},
        ]
        changed, passed = [], False
        for round_index in range(6):
            await self.handle.checkpoint()
            roles = getattr(self.context, "model_routes", None) or getattr(
                self.app.state, "model_roles", None
            )
            from app.services.llm.routing.contracts import TaskCapabilityResolver

            route_options = (
                {
                    "requirements": TaskCapabilityResolver.resolve(
                        "CODING", tools=True, data_kind="ATTACHMENT"
                    )
                }
                if getattr(roles, "intelligent", None)
                else {}
            )
            message = await (
                roles.plan_tools(prompt, tools, role="coding_model", **route_options)
                if roles
                else self.app.state.llm_service.plan_tools(prompt, tools)
            )
            calls = registry.parse_calls(message)
            if not calls:
                break
            prompt.append(message)
            for call in calls:
                await self.handle.checkpoint()
                try:
                    if call["name"] == "code.run":
                        if not requested_tests:
                            raise ValueError("当前用户仅要求代码草稿，未要求运行代码")
                        runner = getattr(executor, "run_script", None)
                        result = await asyncio.to_thread(
                            runner or executor._run_python,
                            call["arguments"]["path"]
                            if runner
                            else executor._path(call["arguments"]["path"]),
                            lambda output: self.publish(
                                "terminal", "项目测试", {"output": output, "status": "executing"}
                            ),
                            lambda: self.handle.cancelled,
                            lambda: self.handle.paused,
                        )
                        self.tests.append(result)
                        passed = result["exit_code"] == 0
                    else:
                        result = await asyncio.to_thread(executor.execute, call)
                    path = call["arguments"].get("path", "项目文件")
                    if "diff" in result:
                        self.patch += result["diff"]
                        changed.append(path)
                        passed = False
                        self.publish("code", path, result)
                        if path.lower().endswith(".html"):
                            self.publish(
                                "preview",
                                "项目预览",
                                {"path": path, "html": call["arguments"]["content"][:24000]},
                            )
                    elif "content" in result or "items" in result:
                        self.publish("file", path, result)
                    if "output" in result:
                        self.publish("terminal", "项目测试", result)
                except Exception as exc:
                    result = {"error": str(exc)[:400]}
                import json

                prompt.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if changed and (passed or not requested_tests):
                break
            self.app.state.work_state.emit(
                self.context.work_session_id,
                "replanning",
                {"summary": "结合文件与测试结果调整编码计划", "round": round_index + 1},
                status="planning",
            )
        verified = bool(changed) and (passed or not requested_tests)
        self.status = "completed" if verified else "failed"
        return {
            "provider": "internal",
            "changed_files": changed if apply else [],
            "draft_files": [] if apply else changed,
            "execution_mode": "apply" if apply else "draft",
            "tests": self.tests,
            "verified": verified,
            **(
                {"error": "六轮内未得到所需代码或测试证据"}
                if not verified
                else {
                    "summary": (
                        "项目代码已修改" if apply else "代码草稿已放在工作区，未修改项目文件"
                    )
                    + ("，测试通过" if requested_tests else "")
                }
            ),
        }


class CodingAgentService:
    def __init__(self, app, context, handle):
        self.app, self.context, self.handle = app, context, handle

    async def run(self, goal):
        def publish(kind, title, data):
            self.app.state.work_state.module(self.context.work_session_id, kind, title, data)

        provider = read_preferences()["coding_provider"]
        executor = self.context.executor
        from app.services.coding.write_intent import CodingWriteIntentResolver

        mode = CodingWriteIntentResolver.execution_mode(
            getattr(self.context, "coding_mode", "virtual_only")
        )
        engine = (
            CodexProvider(
                executor.workspace if mode == "apply" else executor.prepare_sandbox(),
                self.handle,
                publish,
                execution_mode=mode,
                draft_executor=executor,
            )
            if provider == "codex"
            else InternalCodingProvider(self.app, self.context, self.handle, publish)
        )
        return await engine.create_task(goal)

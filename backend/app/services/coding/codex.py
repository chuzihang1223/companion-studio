"""The logged-in Codex CLI, with a selected workspace and filtered JSON events."""

import asyncio
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from app.services.coding.provider import CodingAgentProvider
from app.services.coding.process_control import ProcessTree
from app.core.file_logging import file_logger
from app.services.workspace_paths import scoped_path

EXCLUDED = {".git", ".venv", "node_modules", "__pycache__", ".codex", ".aws"}


def snapshot(root):
    result = {}
    inspected = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        safe_dirs = []
        for name in dirs:
            path = Path(current, name)
            if (
                name in EXCLUDED
                or path.is_symlink()
                or (hasattr(path, "is_junction") and path.is_junction())
            ):
                continue
            try:
                scoped_path(root, str(path.relative_to(root)))
                safe_dirs.append(name)
            except (ValueError, OSError):
                pass
        dirs[:] = safe_dirs
        for name in files:
            inspected += 1
            if inspected > 2000 or len(result) >= 300:
                return result
            path = Path(current, name)
            relative = path.relative_to(root)
            if any(p in name.lower() for p in ("auth", "secret", "credential")):
                continue
            try:
                path = scoped_path(root, str(relative))
                if path.is_file() and path.stat().st_size < 200_000:
                    result[str(relative)] = path.read_text(encoding="utf-8")
            except (ValueError, UnicodeError, OSError):
                pass
    return result


def find_codex():
    explicit = os.getenv("COMPANION_CODEX_EXE")
    candidate = shutil.which("codex")
    if explicit and Path(explicit).is_file():
        return explicit
    if candidate:
        return candidate
    root = Path(os.getenv("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    return next((str(p) for p in root.glob("*/codex.exe") if p.is_file()), None)


class CodexProvider(CodingAgentProvider):
    def __init__(self, workspace, handle, publish, *, execution_mode="draft", draft_executor=None):
        super().__init__()
        if execution_mode not in {"analyze_only", "draft", "apply"}:
            raise ValueError("无效 Codex 执行模式")
        self.execution_mode, self.draft_executor = execution_mode, draft_executor
        self.workspace, self.handle = workspace, handle

        def recorded(kind, title, data):
            self.events.append({"kind": kind, "title": title, "data": data})
            publish(kind, title, data)

        self.publish = recorded
        self.process = None
        self.logger = file_logger("app.codex", "codex.log")

    async def create_task(self, goal):
        executable = find_codex()
        if not executable:
            return {"verified": False, "error": "未安装 Codex CLI"}
        before = snapshot(self.workspace)
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "USERPROFILE",
                "LOCALAPPDATA",
                "APPDATA",
                "PROCESSOR_ARCHITECTURE",
            }
        }
        env.update(TEMP=str(self.workspace), TMP=str(self.workspace), PYTHONDONTWRITEBYTECODE="1")
        command = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only" if self.execution_mode == "analyze_only" else "workspace-write",
            "--skip-git-repo-check",
            "-C",
            str(self.workspace),
            "-c",
            'approval_policy="never"',
            "-",
        ]
        self.status = "executing"
        self.logger.info("task_started provider=codex")
        self.process = await asyncio.create_subprocess_exec(
            *command,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        tree = ProcessTree(self.process.pid)
        behavior = (
            "Analyze only. Do not modify any file. Provide your public findings and proposed code in fenced blocks."
            if self.execution_mode == "analyze_only"
            else "This is a temporary draft copy, never the actual project. Generate the requested code here."
            if self.execution_mode == "draft"
            else "Apply the explicitly requested changes in this project."
        )
        prompt = (
            "Work only inside this workspace. Do not access authentication, secrets, other projects or application data. "
            "Do not use the network or install dependencies. "
            + behavior
            + " Run tests only if the user requested them; report actual results.\nTask: "
            + goal
        )
        self.process.stdin.write(prompt.encode("utf-8"))
        await self.process.stdin.drain()
        self.process.stdin.close()
        started = time.monotonic()
        stderr_task = asyncio.create_task(self._diagnostics())
        error, public_reply = None, ""
        try:
            # Drain buffered JSON events even if a short CLI task already exited.
            while True:
                if self.handle.cancelled:
                    await self.cancel_task()
                    raise RuntimeError("任务已停止")
                if self.handle.paused:
                    tree.pause()
                    try:
                        await self.handle.checkpoint()
                    finally:
                        tree.resume()
                if time.monotonic() - started > 360:
                    await self.cancel_task()
                    raise TimeoutError("Codex 任务超过六分钟，已停止")
                try:
                    line = await asyncio.wait_for(self.process.stdout.readline(), 0.5)
                except asyncio.TimeoutError:
                    continue
                if not line:
                    break
                try:
                    data = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if data.get("type") in {"error", "turn.failed"}:
                    error = str(data.get("message") or data.get("error") or "Codex 请求失败")[:400]
                item = data.get("item") or {}
                # Private reasoning and raw model output are never stored or displayed.
                if item.get("type") == "command_execution":
                    output = str(item.get("aggregated_output") or "")[-12_000:]
                    detail = {
                        "command": str(item.get("command") or "")[:1000],
                        "output": output,
                        "exit_code": item.get("exit_code"),
                        "status": item.get("status"),
                    }
                    self.publish("terminal", "Codex 终端", detail)
                    if data.get("type") == "item.completed":
                        self.tests.append(detail)
                elif item.get("type") == "file_change":
                    self.publish("file", "Codex 项目文件", {"changes": item.get("changes", [])})
                elif item.get("type") == "agent_message" and data.get("type") == "item.completed":
                    public_reply = str(item.get("text") or "")[:24_000]
            await self.process.wait()
        finally:
            tree.resume()
            if self.handle.cancelled:
                await self.cancel_task()
            diagnostic = await stderr_task
        after = snapshot(self.workspace)
        changes = []
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                patch = "".join(
                    difflib.unified_diff(
                        before.get(name, "").splitlines(True),
                        after.get(name, "").splitlines(True),
                        fromfile="a/" + name,
                        tofile="b/" + name,
                    )
                )
                self.patch += patch
                changes.append(name)
                if self.execution_mode != "apply" and self.draft_executor:
                    result = self.draft_executor.execute(
                        {
                            "name": "code.edit_file" if name in before else "code.create_file",
                            "arguments": {"path": name, "content": after.get(name, "")},
                        }
                    )
                    self.publish("code", name, result)
                else:
                    self.publish("code", name, {"path": name, "diff": patch[:16_000]})
        if self.execution_mode == "analyze_only" and public_reply:
            self.publish("artifacts", "代码分析", {"summary": public_reply})
            if self.draft_executor:
                import re

                for index, (language, content) in enumerate(
                    re.findall(r"```([^\n`]*)\n([\s\S]*?)```", public_reply)
                ):
                    suffix = {
                        "python": ".py",
                        "javascript": ".js",
                        "typescript": ".ts",
                        "html": ".html",
                    }.get(language.strip(), ".txt")
                    title = "analysis-" + str(index + 1) + suffix
                    result = self.draft_executor.execute(
                        {
                            "name": "code.create_file",
                            "arguments": {"path": title, "content": content},
                        }
                    )
                    self.publish("code", title, result)
        # A successful CLI turn alone does not establish project correctness.
        tested = any(
            t.get("exit_code") == 0
            and any(word in t["command"].lower() for word in ("test", "pytest", "unittest"))
            for t in self.tests
        )
        needs_tests = any(word in goal.lower() for word in ("测试", "运行", "test", "run "))
        evidence = bool(public_reply) if self.execution_mode == "analyze_only" else bool(changes)
        verified = (
            self.process.returncode == 0 and not error and evidence and (tested or not needs_tests)
        )
        self.status = "completed" if verified else "failed"
        self.logger.info(
            "task_finished status=%s changed_files=%s tests=%s",
            self.status,
            len(changes),
            len(self.tests),
        )
        return {
            "provider": "codex",
            "verified": verified,
            "execution_mode": self.execution_mode,
            "changed_files": changes if self.execution_mode == "apply" else [],
            "draft_files": changes if self.execution_mode == "draft" else [],
            "tests": self.tests[-8:],
            "summary": (
                "Codex 已完成代码分析"
                if self.execution_mode == "analyze_only"
                else "Codex 已生成草稿，未修改项目文件"
                if self.execution_mode == "draft"
                else "Codex 已修改项目文件"
            )
            if verified
            else "Codex 未得到完整的任务验证证据",
            **(
                {"error": error or diagnostic or "未得到完整的文件与测试证据"}
                if not verified
                else {}
            ),
        }

    async def _diagnostics(self):
        # Do not persist raw diagnostics, which may include account identifiers.
        await self.process.stderr.read()
        return (
            "Codex 执行失败，请检查 CLI 登录或沙箱配置"
            if self.process.returncode not in (None, 0)
            else ""
        )

    async def cancel_task(self):
        if self.process and self.process.returncode is None:
            if os.name == "nt":
                child = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(self.process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                await child.wait()
            else:
                self.process.kill()
            await self.process.wait()

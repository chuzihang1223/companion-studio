"""有界调度：最多一个评测批次，Mock/Sandbox 并发至多二，GUI/Live 串行。

暂停只停止出队，不伪装暂停外部模型；取消/截止时间结束自有 worker Job，
未执行项不混入成功率。读取历史从不调用本引擎的 start。
"""

import asyncio
from collections import defaultdict
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

from app.core.config import settings
from app.schemas.evaluation import Mode
from app.services.agent_tools import ToolRegistry
from app.services.evaluation.environment import EnvironmentManager, WorkerFailure
from app.services.evaluation.registry import BenchmarkRegistry
from app.services.evaluation.repository import EvaluationRepository, TERMINAL, empty_result
from app.services.trace.privacy import identifier


def build_identity():
    if getattr(sys, "frozen", False):
        path = settings.project_root / "build-info.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return (
                data.get("version", "unknown"),
                data.get("source_revision", "unknown"),
                data.get("prompt_version", "unknown"),
            )
        return "unknown", "unknown", "unknown"
    try:
        version = json.loads(
            (settings.project_root / "desktop/package.json").read_text(encoding="utf-8")
        )["version"]
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=settings.project_root,
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        ).stdout.strip()
        prompt = ":".join(
            hashlib.sha256((settings.project_root / relative).read_bytes()).hexdigest()
            for relative in (
                "backend/app/services/character/prompt_builder.py",
                "backend/app/services/agent_service.py",
            )
        )
        return version, commit or "unknown", prompt
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unknown", "unknown", "unknown"


class AgentEvaluationEngine:
    def __init__(self, root=None, repository=None):
        self.registry = BenchmarkRegistry()
        self.repository = repository or EvaluationRepository()
        self.environment = EnvironmentManager(root or settings.data_root / "evaluation")
        self.tasks = {}
        self.pauses = {}
        self.subscribers = defaultdict(set)
        self.lock = asyncio.Lock()

    async def initialize(self):
        await asyncio.to_thread(self.repository.recover)
        await asyncio.to_thread(self.environment.cleanup_abandoned, self.repository)
        # Retention is bounded by 30 days and 100 runs. Active work is never pruned.
        old = await asyncio.to_thread(self.repository.listing, 0, 100)
        for row in old["items"]:
            date = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc)
            if row["status"] in TERMINAL and date < datetime.now(timezone.utc) - timedelta(days=30):
                await self.remove(row["run_id"])

    async def start(self, options, app):
        async with self.lock:
            self.registry = BenchmarkRegistry(options.dataset_id)
            cases = self.registry.select(options.case_ids)
            model_config = None
            if options.mode == Mode.LIVE:
                service = app.state.model_roles.service(options.model_role, tools=True)
                if (
                    not service.config
                    or not service.config.model
                    or service.config.model == "unconfigured"
                ):
                    raise ValueError("请先在设置配置可用模型")
                model_config = service.config.model_dump()
                # No streaming in this bounded runner; max completion reservation
                # is separate from observed prompt+completion total Token usage.
                model_config["max_tokens"] = min(model_config.get("max_tokens") or 2048, 2048)
                model_config["timeout_seconds"] = max(
                    5, min(options.timeout_seconds, model_config["timeout_seconds"])
                )
            model = (
                {
                    "provider": model_config["provider"],
                    "model_id": identifier(model_config["model"]),
                    "temperature": model_config["temperature"],
                    "max_tokens": model_config["max_tokens"],
                    "timeout_seconds": model_config["timeout_seconds"],
                    "tool_calling": model_config["capabilities"].get("tool_calling"),
                }
                if model_config
                else {
                    "provider": "fixture",
                    "model_id": "deterministic-mock",
                    "temperature": None,
                    "max_tokens": None,
                    "timeout_seconds": options.timeout_seconds,
                    "tool_calling": True,
                }
            )
            version, commit, prompt_version = await asyncio.to_thread(build_identity)
            snapshot = {
                "dataset_id": self.registry.dataset_id,
                "dataset_version": self.registry.version,
                "workflow_schema_version": 1
                if self.registry.dataset_id == "workflow-core"
                else None,
                "workflow_templates_version": "1"
                if self.registry.dataset_id == "workflow-core"
                else None,
                "dataset_sha256": self.registry.digest,
                "case_ids": [c.case_id for c in cases],
                "repetitions": options.repetitions,
                "mode": options.mode.value,
                "model": model,
                "app_version": version,
                "git_commit": commit,
                "prompt_version": prompt_version,
                "tool_schema_sha256": hashlib.sha256(
                    json.dumps(ToolRegistry().schemas(), sort_keys=True).encode()
                ).hexdigest(),
                "timeout_seconds": options.timeout_seconds,
                "concurrency": options.concurrency,
                "max_duration_seconds": options.max_duration_seconds,
                "max_requests": options.max_requests,
                "token_budget": options.token_budget,
                "environment": {
                    "os": platform.system(),
                    "os_version": platform.version(),
                    "python": platform.python_version(),
                    "fixture": "studio-fixture-v1"
                    if self.registry.dataset_id == "studio-core"
                    else "recovery-fixture-v1"
                    if self.registry.dataset_id == "recovery-core"
                    else "mcp-fixture-v1",
                    "desktop_bridge": "simulated",
                    "process_isolation": True,
                },
            }
            run = await asyncio.to_thread(self.repository.create, options, cases, snapshot)
            run_id = run["run_id"]
            gate = asyncio.Event()
            gate.set()
            self.pauses[run_id] = gate
            self.tasks[run_id] = asyncio.create_task(self._run(run_id, options, model_config))
            return run

    def publish(self, run_id, event, case_id=None, attempt_id=None):
        data = {"run_id": run_id, "case_id": case_id, "attempt_id": attempt_id}
        for queue in list(self.subscribers.get(run_id, set())):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait((event, data))

    async def _run(self, run_id, options, model_config):
        final_status = "COMPLETED"
        try:
            queue = asyncio.Queue()
            remaining_requests, remaining_tokens = options.max_requests, options.token_budget
            rows = (await asyncio.to_thread(self.repository.results, run_id, 0, 100))["items"]
            for row in rows:
                queue.put_nowait(row)
            await asyncio.to_thread(self.repository.status, run_id, "RUNNING")
            self.publish(run_id, "EVAL_RUN_STARTED")

            async def consume():
                nonlocal remaining_requests, remaining_tokens
                while not queue.empty():
                    await self.pauses[run_id].wait()
                    with suppress(asyncio.QueueEmpty):
                        row = queue.get_nowait()
                        attempt_id, case_id = row["attempt_id"], row["case_id"]
                        case = self.registry.by_id[case_id]
                        await asyncio.to_thread(self.repository.started, attempt_id)
                        self.publish(run_id, "EVAL_CASE_STARTED", case_id, attempt_id)
                        links = None
                        if options.mode not in case.execution_mode:
                            result = empty_result("BLOCKED", "MODE_NOT_APPLICABLE")
                        elif options.mode == Mode.WINDOWS:
                            result = empty_result(
                                "BLOCKED", "INTERACTIVE_FIXTURE_BRIDGE_UNAVAILABLE"
                            )
                        elif options.mode == Mode.LIVE and (
                            remaining_requests <= 0 or remaining_tokens <= 0
                        ):
                            result = empty_result("BLOCKED", "MODEL_BUDGET_EXHAUSTED")
                        else:
                            payload = {
                                "dataset_id": self.registry.dataset_id,
                                "case_id": case_id,
                                "mode": options.mode.value,
                                "max_requests": remaining_requests,
                                "token_budget": remaining_tokens,
                                "request_tokens": (model_config or {}).get("max_tokens", 2048),
                            }
                            if model_config:
                                payload["model_config"] = model_config
                            try:
                                value = await self.environment.run(
                                    run_id,
                                    attempt_id,
                                    payload,
                                    min(case.timeout_seconds, options.timeout_seconds),
                                )
                                result, links = value["result"], value["links"]
                                if options.mode == Mode.LIVE:
                                    budget = value["budget"]
                                    remaining_requests -= budget["requests"]
                                    remaining_tokens -= (
                                        budget.get("total_tokens") or remaining_tokens
                                    )
                            except asyncio.CancelledError:
                                links = await asyncio.to_thread(
                                    self.environment.recover_links, run_id, attempt_id
                                )
                                await asyncio.to_thread(
                                    self.repository.finish,
                                    run_id,
                                    attempt_id,
                                    empty_result("CANCELLED", "CANCELLED"),
                                    links,
                                )
                                raise
                            except TimeoutError:
                                result = empty_result("UNKNOWN", "TIMEOUT")
                                links = await asyncio.to_thread(
                                    self.environment.recover_links, run_id, attempt_id
                                )
                                if options.mode == Mode.LIVE:
                                    remaining_requests = 0
                            except Exception as exc:
                                result = empty_result(
                                    "BLOCKED"
                                    if isinstance(exc, WorkerFailure)
                                    and exc.code == "MODEL_BUDGET_EXHAUSTED"
                                    else "ERROR",
                                    exc.code if isinstance(exc, WorkerFailure) else "WORKER_ERROR",
                                )
                                links = await asyncio.to_thread(
                                    self.environment.recover_links, run_id, attempt_id
                                )
                                if options.mode == Mode.LIVE:
                                    remaining_requests = 0
                        await asyncio.to_thread(
                            self.repository.finish, run_id, attempt_id, result, links
                        )
                        await asyncio.to_thread(self.repository.refresh_metrics, run_id)
                        self.publish(
                            run_id,
                            "EVAL_CASE_FAILED"
                            if result["completion_status"] in {"FAIL", "ERROR", "UNKNOWN"}
                            else "EVAL_CASE_COMPLETED",
                            case_id,
                            attempt_id,
                        )
                        self.publish(run_id, "EVAL_RUN_PROGRESS", case_id, attempt_id)

            async with asyncio.timeout(options.max_duration_seconds):
                async with asyncio.TaskGroup() as group:
                    for _ in range(options.concurrency):
                        group.create_task(consume())
        except asyncio.CancelledError:
            final_status = "CANCELLED"
        except TimeoutError:
            final_status = "FAILED"
        except Exception:
            final_status = "FAILED"
        finally:
            await self._finalize(run_id, final_status)

    async def _finalize(self, run_id, final_status):
        for row in (await asyncio.to_thread(self.repository.results, run_id, 0, 100))["items"]:
            if row["grader_status"] == "PENDING":
                await asyncio.to_thread(
                    self.repository.finish,
                    run_id,
                    row["attempt_id"],
                    empty_result(
                        "NOT_RUN",
                        "CANCELLED" if final_status == "CANCELLED" else "RUN_INTERRUPTED",
                    ),
                )
        await asyncio.to_thread(self.repository.refresh_metrics, run_id)
        await asyncio.to_thread(self.repository.status, run_id, final_status)
        self.publish(run_id, "EVAL_RUN_" + final_status)
        self.tasks.pop(run_id, None)
        self.pauses.pop(run_id, None)

    async def control(self, run_id, action):
        run = await asyncio.to_thread(self.repository.detail, run_id)
        if run["status"] in TERMINAL:
            raise ValueError("评测已经结束")
        if action == "cancel":
            task = self.tasks.get(run_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if (await asyncio.to_thread(self.repository.detail, run_id))["status"] not in TERMINAL:
                await self._finalize(run_id, "CANCELLED")
        elif action in {"pause", "resume"}:
            gate = self.pauses.get(run_id)
            if not gate:
                raise ValueError("评测任务不可用")
            gate.set() if action == "resume" else gate.clear()
            await asyncio.to_thread(
                self.repository.status, run_id, "RUNNING" if action == "resume" else "PAUSED"
            )
            self.publish(run_id, "EVAL_RUN_PROGRESS")
        return await asyncio.to_thread(self.repository.detail, run_id)

    async def remove(self, run_id):
        async with self.lock:
            result = await asyncio.to_thread(self.repository.remove, run_id)
            await asyncio.to_thread(self.environment.remove, run_id)
            return result

    async def stream(self, run_id):
        await asyncio.to_thread(self.repository.detail, run_id)
        queue = asyncio.Queue(maxsize=32)
        self.subscribers[run_id].add(queue)
        try:
            self.publish(run_id, "EVAL_RUN_PROGRESS")
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), 10)
                    yield "event: " + event + "\ndata: " + json.dumps(data) + "\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
                if (await asyncio.to_thread(self.repository.detail, run_id))[
                    "status"
                ] in TERMINAL and queue.empty():
                    break
        finally:
            self.subscribers[run_id].discard(queue)
            if not self.subscribers[run_id]:
                self.subscribers.pop(run_id, None)

    async def close(self):
        owned = list(self.tasks.items())
        for _, task in owned:
            task.cancel()
        await asyncio.gather(*(task for _, task in owned), return_exceptions=True)
        for run_id, _ in owned:
            if (await asyncio.to_thread(self.repository.detail, run_id))["status"] not in TERMINAL:
                await self._finalize(run_id, "CANCELLED")

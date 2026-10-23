"""Owned cross-skill host: production services, synthetic HTTP/desktop/speech boundaries."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from scheduler_fixture import scheduler_host
from app.core.config import settings
from app.database.session import SessionLocal
from app.services.character.loader import CharacterLoader
from app.services.evaluation.router_fixtures import RouterFixture
from app.services.knowledge.service import KnowledgeService
from app.database.session import engine
from app.services.llm.routing.budget import BudgetLedger
from app.services.realtime_voice.sessions import VoiceSessionManager


class SyntheticSpeech:
    name = "synthetic-unified-pcm"
    config = SimpleNamespace(speed=1)

    def __init__(self, **options):
        self.calls = 0
        self.config = SimpleNamespace(
            speed=options.get("speed", 1),
            voice_id=options.get("voice_id", ""),
            language=options.get("language", "zh-CN"),
        )

    async def recognize(self, wav):
        if wav[:4] != b"RIFF":
            raise ValueError("SYNTHETIC_WAV_REQUIRED")
        self.calls += 1
        return "请读取选定项目中的 fixture.txt，再报告实际内容。"

    async def synthesize_live(self, queue, speed, on_pcm):
        parts = []
        while (text := await queue.get()) is not None:
            if text:
                pcm = b"\x20\x00\xe0\xff" * 800
                await on_pcm(pcm, 16000, 1)
                parts.append(pcm)
        return parts, 16000, "synthetic"

    async def cancel(self):
        pass

    async def close(self):
        pass


class UnifiedHost:
    def __init__(self, app, owner, scheduler, clock, trace, workspace):
        self.app, self.owner, self.scheduler, self.clock = app, owner, scheduler, clock
        self.trace, self.workspace = trace, workspace
        self.tool = ("workspace_read", {"path": "fixture.txt"})
        self.requests = []
        self.fixture = RouterFixture(app, BudgetLedger(SessionLocal))
        self.fixture.add(responder=self.respond)
        self.fixture.state()
        app.state.llm_service = self.fixture.models[0][1]

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        content = "Synthetic unified report based on the tool observation."
        for message in body.get("messages", []):
            value = message.get("content", "")
            marker = "UNTRUSTED_KNOWLEDGE_DATA\n"
            if isinstance(value, str) and marker in value:
                rows = json.loads(value.split(marker)[-1])
                content = "Orchard launch port is 8794. [K:" + rows[0]["chunk_id"] + "]"
        message = {"role": "assistant", "content": content}
        if body.get("tools") and self.tool:
            name, arguments = self.tool
            advertised = {tool["function"]["name"] for tool in body["tools"]}
            if name not in advertised:
                raise AssertionError(
                    "Synthetic planner selected a tool outside the advertised schema: " + name
                )
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "unified-call",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        usage = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
        if body.get("stream"):
            return httpx.Response(
                200,
                text="data: "
                + json.dumps({"choices": [{"delta": {"content": content}}]})
                + "\n\ndata: "
                + json.dumps({"choices": [], "usage": usage})
                + "\n\ndata: [DONE]\n\n",
            )
        return httpx.Response(200, json={"choices": [{"message": message}], "usage": usage})

    async def agent(self, text="请读取所选项目中的 fixture.txt 并根据实际结果报告。", routes=None):
        options = {"model_routes": routes} if routes else {}
        plan = await self.app.state.agent_service.plan(
            self.app, "assistant", self.owner, text, **options
        )
        result = await self.app.state.agent_service.execute(
            self.app, plan["plan_id"], {call["id"] for call in plan["calls"]}, "natural", False, 1.0
        )
        await self.trace.flush()
        return plan, result

    async def connect_mcp(self):
        import sys
        from app.services.mcp_ext.contracts import ServerConfig, digest

        manager = self.app.state.mcp_connections
        config = ServerConfig(
            display_name="Unified synthetic MCP",
            transport="stdio",
            endpoint_or_command=sys.executable,
            args=["-B", str(settings.project_root / "backend/run.py"), "--mcp-demo"],
            enabled=True,
        )
        server = manager.save(config)
        await manager.connect(server, approved_fingerprint=digest(config.model_dump()))
        registry = self.app.state.external_tools
        await registry.discover(server)
        tool = next(row for row in registry.listing(server) if row["name"] == "echo")
        registry.configure(tool["id"], True, "READ_ONLY_EXTERNAL")
        return tool

    async def indexed(self):
        from app.services.knowledge.contracts import CreateCollection
        from app.services.knowledge.context import model_target

        root = self.workspace / "sources"
        root.mkdir(exist_ok=True)
        (root / "proof.md").write_text(
            "Orchard launch port is 8794. Original synthetic test evidence.", encoding="utf-8"
        )
        service = self.app.state.knowledge
        row = service.store.create(CreateCollection(name="Unified public synthetic sources"))
        service.store.update(row["id"], 1, add_root=str(root))
        service.store.grant(
            self.owner, "assistant", [row["id"]], [model_target(self.app.state.llm_service)]
        )
        key = await service.indexer.start(row["id"])
        await service.indexer.tasks[key]
        return row["id"]


@asynccontextmanager
async def unified_host(root):
    async with scheduler_host(root) as (app, scheduler, owner, clock, trace):
        app.state.characters = CharacterLoader(settings.project_root / "characters")
        app.state.characters.load()
        app.state.knowledge = KnowledgeService(engine, app.state.trace_recorder)
        app.state.realtime_voice = VoiceSessionManager(app, SyntheticSpeech)
        app.state.voice_client = SyntheticSpeech()
        host = UnifiedHost(app, owner, scheduler, clock, trace, root / "workspace")
        try:
            yield host
        finally:
            await app.state.realtime_voice.close()
            await app.state.knowledge.close()

import asyncio
import json

from fastapi import FastAPI
import httpx
import pytest

from app.api.extensions import router
from app.services.mcp_ext.contracts import digest
from app.services.mcp_ext.registry import ExternalToolRegistry
from app.services.skills.manager import AgentSkillManager
from test_mcp_connection import host, config
from test_skill_security import VALID


def test_protected_api_real_resources_prompts_and_skill_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "local-extension-test")

    async def run():
        manager = host(tmp_path)
        app = FastAPI()
        app.include_router(router)
        app.state.mcp_connections = manager
        app.state.extension_store = manager.store
        app.state.external_tools = ExternalToolRegistry(manager.store, manager)
        app.state.agent_skills = AgentSkillManager(manager.store, tmp_path / "skills")
        headers = {"X-Companion-Control-Token": "local-extension-test"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            assert (await client.get("/api/mcp/servers")).status_code == 403
            assert (await client.get("/api/skills")).status_code == 403
            client.headers.update(headers)
            created = await client.post("/api/mcp/servers", json=config().model_dump())
            assert created.status_code == 200, created.text
            server = created.json()
            route = "/api/mcp/servers/" + server["server_id"]
            assert (
                await client.post(
                    route + "/connect", json={"approved_fingerprint": server["config_fingerprint"]}
                )
            ).status_code == 200
            try:
                entries = await client.get(route + "/catalog")
                assert entries.status_code == 200, entries.text
                assert entries.json()["resources"][0]["uri"] == "fixture://readme"
                denied = await client.post(
                    route + "/preview", json={"kind": "resource", "name": "fixture://readme"}
                )
                assert denied.status_code == 403
                denied = await client.post(
                    route + "/preview",
                    json={"kind": "resource", "name": "file:///private", "approved": True},
                )
                assert denied.status_code == 403
                resource = await client.post(
                    route + "/preview",
                    json={"kind": "resource", "name": "fixture://readme", "approved": True},
                )
                assert resource.status_code == 200, resource.text
                assert resource.json()["trust"] == "untrusted_external_data"
                prompt = await client.post(
                    route + "/preview",
                    json={
                        "kind": "prompt",
                        "name": "summarize",
                        "arguments": {"topic": "synthetic"},
                        "approved": True,
                    },
                )
                assert prompt.status_code == 200 and prompt.json()["auto_execute"] is False
                assert "synthetic" in prompt.text
                cred = await client.post(
                    route + "/credentials",
                    json={"config_fingerprint": "0" * 64, "token": "isolated-test-key"},
                )
                assert cred.status_code == 409
                cred = await client.post(
                    route + "/credentials",
                    json={
                        "config_fingerprint": server["config_fingerprint"],
                        "token": "isolated-test-key",
                    },
                )
                assert cred.status_code == 200
                assert "isolated-test-key" not in (await client.get("/api/mcp/servers")).text
                assert (
                    "isolated-test-key" not in (await client.get("/api/mcp/export-template")).text
                )
                source = tmp_path / "project-review"
                source.mkdir()
                (source / "SKILL.md").write_text(VALID, encoding="utf-8")
                preview = await client.post("/api/skills/preview", json={"folder": str(source)})
                assert preview.status_code == 200
                imported = await client.post(
                    "/api/skills/import",
                    json={"folder": str(source), "expected_hash": preview.json()["content_hash"]},
                )
                assert imported.status_code == 200
                skill = imported.json()
                assert not skill["enabled"]
                detail = await client.get("/api/skills/" + skill["id"])
                assert "Read selected" in detail.json()["instructions"]
                assert (
                    await client.patch("/api/skills/" + skill["id"], json={"enabled": True})
                ).json()["enabled"]
                assert (await client.delete("/api/skills/" + skill["id"])).status_code == 200
            finally:
                await manager.close()
            assert all(
                c.transport.process.returncode is not None for c in manager.connections.values()
            )

    asyncio.run(run())

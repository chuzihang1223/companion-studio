import asyncio
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpx

from app.api.knowledge import router
from app.services.knowledge.service import KnowledgeService


def test_api_control_token_native_grant_and_owner(corpus, monkeypatch):
    store, root, owner = corpus
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "isolated-test-key")
    app = FastAPI()
    app.include_router(router)
    app.state.knowledge = KnowledgeService(store.engine)
    model = SimpleNamespace(
        config=SimpleNamespace(
            provider="fixture", base_url="http://127.0.0.1:9", model="local-fixture"
        )
    )
    app.state.model_roles = SimpleNamespace(service=lambda _role: model)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://fixture"
        ) as client:
            assert (await client.get("/api/knowledge/collections")).status_code == 403
            client.headers["X-Companion-Control-Token"] = "isolated-test-key"
            item = (
                await client.post("/api/knowledge/collections", json={"name": "API fixture"})
            ).json()
            path = "/api/knowledge/collections/" + item["id"]
            updated = await client.patch(path, json={"revision": 1, "add_root": str(root)})
            assert updated.status_code == 200, updated.text
            assert (
                await client.patch(path, json={"revision": 1, "add_root": str(root)})
            ).status_code == 409
            payload = {
                "conversation_id": owner,
                "character_id": "assistant",
                "search": {"query": "test", "collection_ids": [item["id"]]},
            }
            assert (await client.post("/api/knowledge/search", json=payload)).status_code == 403
            grant = {
                "conversation_id": owner,
                "character_id": "assistant",
                "collection_ids": [item["id"]],
            }
            assert (await client.post("/api/knowledge/selection", json=grant)).status_code == 200
            assert (await client.post("/api/knowledge/search", json=payload)).status_code == 200
            assert (
                await client.post(
                    "/api/knowledge/selection", json=grant | {"allow_model_transfer": True}
                )
            ).status_code == 403
            targets = (await client.get("/api/knowledge/targets")).json()["targets"]
            assert (
                await client.post(
                    "/api/knowledge/selection",
                    json=grant
                    | {
                        "allow_model_transfer": True,
                        "expected_targets": list({r["fingerprint"] for r in targets}),
                    },
                )
            ).status_code == 200
            assert (
                await client.post(
                    "/api/knowledge/search", json=payload | {"conversation_id": str(uuid4())}
                )
            ).status_code == 403
            memory = {
                "conversation_id": owner,
                "character_id": "assistant",
                "memory": {
                    "scope": "CONVERSATION",
                    "owner_id": owner,
                    "content": "我偏好中文回答。",
                },
            }
            assert (await client.post("/api/knowledge/memories", json=memory)).status_code == 200
            bad = await client.post(
                "/api/knowledge/memories",
                json=memory | {"memory": memory["memory"] | {"owner_id": str(uuid4())}},
            )
            assert bad.status_code == 403

    asyncio.run(run())

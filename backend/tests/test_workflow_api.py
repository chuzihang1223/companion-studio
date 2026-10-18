import asyncio
from uuid import uuid4
import httpx
from app.api.workflows import router
from app.services.workflows.contracts import Definition
from workflow_fixture import host, graph


def test_controlled_workflow_api_owner_revisions_and_events(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "workflow-local-fixture")

    async def run():
        async with host(tmp_path) as (app, owner, _):
            app.include_router(router)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture"
            ) as client:
                assert (await client.get("/api/workflows")).status_code == 403
                client.headers["X-Companion-Control-Token"] = "workflow-local-fixture"
                definition = graph().model_dump(mode="json")
                created = await client.post(
                    "/api/workflows", json={"definition": definition, "revision": 0}
                )
                assert created.status_code == 200, created.text
                route = "/api/workflows/" + definition["workflow_id"]
                saved = created.json()
                assert (
                    await client.patch(route, json={"definition": definition, "revision": 42})
                ).status_code == 409
                bad = definition | {"workflow_id": str(uuid4())}
                assert (
                    await client.patch(route, json={"definition": bad, "revision": 1})
                ).status_code == 422
                assert (
                    await client.post(route + "/validate", json=saved | {"updated_at": "no"})
                ).status_code == 422
                validated = await client.post(
                    route + "/validate",
                    json={"definition": definition, "revision": saved["revision"]},
                )
                assert validated.status_code == 200, validated.text
                published = await client.post(
                    route + "/publish",
                    json={"definition": definition, "revision": saved["revision"]},
                )
                assert published.status_code == 200, published.text
                version_id = published.json()["version_id"]
                assert (
                    await client.get(
                        "/api/workflow-versions/" + version_id + "/overview",
                        params={"conversation_id": owner},
                    )
                ).json()["grants_permissions"] is False
                start = await client.post(
                    route + "/runs",
                    json={"version_id": version_id, "conversation_id": owner, "inputs": {}},
                )
                assert start.status_code == 200, start.text
                key = start.json()["run_id"]
                from workflow_fixture import settle

                await settle(app, owner, key)
                target = "/api/workflow-runs/" + key
                assert (
                    await client.get(target, params={"conversation_id": str(uuid4())})
                ).status_code == 404
                assert (
                    await client.post(
                        target + "/approve",
                        json={
                            "conversation_id": owner,
                            "approval_id": str(uuid4()),
                            "decision": True,
                        },
                    )
                ).status_code == 409
                data = (await client.get(target, params={"conversation_id": owner})).json()
                assert data["state"] == "COMPLETED"
                events = (
                    await client.get(target + "/events", params={"conversation_id": owner})
                ).json()["items"]
                assert events and len({e["cursor"] for e in events}) == len(events)
                assert not (
                    await client.get(
                        target + "/events",
                        params={"conversation_id": owner, "after": events[-1]["cursor"]},
                    )
                ).json()["items"]
                assert "output" not in events[-1] and "inputs" not in events[-1]
                templates = (await client.get("/api/workflows/templates")).json()["items"]
                assert {row["template_id"] for row in templates} == {
                    "project-knowledge-assistant",
                    "project-review",
                    "code-test-report",
                    "mcp-research",
                    "visual-desktop-automation",
                }
                invalid = Definition().model_dump(mode="json") | {"password": "synthetic"}
                assert (
                    await client.post("/api/workflows", json={"definition": invalid})
                ).status_code == 422

    asyncio.run(run())

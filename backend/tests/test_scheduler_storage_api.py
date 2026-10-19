import asyncio
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.api.schedules import router
from app.database.migrations import migrate
from app.models.schedules import ScheduleRow
from app.services.scheduling.contracts import ScheduleError
from app.services.scheduling.store import ScheduleStore
from scheduler_fixture import scheduler_host, scheduled


def test_additive_migration_preserves_old_rows_and_is_idempotent(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "old.db").as_posix())
    try:
        # An unrelated historical table is deliberately kept byte-for-byte.
        with engine.begin() as db:
            db.execute(text("CREATE TABLE historical_fixture (id INTEGER PRIMARY KEY, body TEXT)"))
            db.execute(text("INSERT INTO historical_fixture VALUES (1, 'KEEP synthetic')"))
        migrate(engine)
        migrate(engine)
        with engine.connect() as db:
            assert db.execute(text("SELECT * FROM historical_fixture")).all() == [
                (1, "KEEP synthetic")
            ]
            assert db.execute(text("SELECT count(*) FROM schedule_definitions")).scalar() == 0
    finally:
        engine.dispose()


def test_sqlite_busy_rolls_back_and_succeeds_after_release(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "busy.db").as_posix(), connect_args={"timeout": 0.05}
    )
    migrate(engine)
    store = ScheduleStore(sessionmaker(engine, expire_on_commit=False))
    try:
        with engine.connect() as blocker:
            blocker.execute(text("BEGIN IMMEDIATE"))
            with pytest.raises(OperationalError):
                store.settings({"paused": True})
            blocker.rollback()
        assert store.settings()["paused"] is False
        assert store.settings({"paused": True})["paused"] is True
    finally:
        engine.dispose()


def test_revision_cas_competing_edits_keep_exactly_one_winner(tmp_path):
    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            original = scheduler.save(definition)

            def edit(n):
                try:
                    return scheduler.save(
                        definition.model_copy(update={"name": "Edit " + str(n)}),
                        original["revision"],
                    )["revision"]
                except ScheduleError:
                    return None

            with ThreadPoolExecutor(max_workers=5) as pool:
                revisions = list(pool.map(edit, range(5)))
            assert revisions.count(2) == 1
            assert scheduler.store.get(definition.schedule_id, owner)["revision"] == 2

    asyncio.run(run())


def test_control_api_owner_binding_invalid_input_and_private_error_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPANION_CONTROL_TOKEN", "synthetic-control")

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            app.include_router(router)
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture"
            ) as client:
                assert (await client.get("/api/scheduler/status")).status_code == 403
                client.headers["X-Companion-Control-Token"] = "synthetic-control"
                url = "/api/scheduler/schedules/" + definition.schedule_id
                assert (
                    await client.get(url + "/history", params={"conversation_id": str(uuid4())})
                ).status_code == 404
                assert (
                    await client.get(url + "/history", params={"conversation_id": owner})
                ).status_code == 200
                invalid = definition.model_dump(mode="json") | {
                    "unknown": "sk-" + "synthetic-only" * 5
                }
                response = await client.post(
                    "/api/scheduler/schedules", json={"definition": invalid}
                )
                assert response.status_code == 422 and "synthetic-only" not in response.text
                occurrence = scheduler.run_now(definition.schedule_id, owner)
                consent = await client.post(
                    "/api/scheduler/occurrences/" + occurrence["occurrence_id"] + "/approve",
                    json={"conversation_id": owner},
                )
                assert consent.status_code == 422

                def unavailable(*_args, **_kwargs):
                    raise OperationalError(
                        "synthetic private path", {}, Exception("synthetic token")
                    )

                monkeypatch.setattr(scheduler, "status", unavailable)
                failed = await client.get("/api/scheduler/status")
                assert failed.status_code == 503 and "synthetic" not in failed.text

    asyncio.run(run())


def test_completed_history_not_changed_by_deleting_schedule(tmp_path):
    from scheduler_fixture import run_to_completion

    async def run():
        async with scheduler_host(tmp_path) as (app, scheduler, owner, clock, _):
            definition = scheduled(app, owner, clock)
            scheduler.save(definition)
            row = scheduler.run_now(definition.schedule_id, owner)
            await scheduler.tick()
            final = await run_to_completion(app, scheduler, owner, row["occurrence_id"])
            saved = scheduler.store.get(definition.schedule_id, owner)
            scheduler.set_state(definition.schedule_id, owner, saved["revision"], "ARCHIVED")
            assert (
                scheduler.store.occurrence(row["occurrence_id"], owner)["status"]
                == final["status"]
                == "COMPLETED"
            )
            with scheduler.store.factory() as db:
                assert db.get(ScheduleRow, definition.schedule_id).state == "ARCHIVED"

    asyncio.run(run())

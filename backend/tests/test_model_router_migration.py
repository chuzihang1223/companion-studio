"""Synthetic pre-router and early local ledger schemas; never a real user profile."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.database.migrations import migrate
from app.services.llm.routing.budget import BudgetLedger


def test_early_router_ledger_additive_migration_preserves_unknown_reservations(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as db:
        db.execute(
            text(
                "CREATE TABLE model_routing_requests (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, workflow_id TEXT, day TEXT NOT NULL, provider_ref TEXT NOT NULL, model_id TEXT NOT NULL, config_version TEXT NOT NULL, currency TEXT NOT NULL, reserved_micros INTEGER, estimated_micros INTEGER, status TEXT NOT NULL, usage_json TEXT)"
            )
        )
        db.execute(
            text(
                "INSERT INTO model_routing_requests VALUES ('old-request','old-task',NULL,'2026-10-23','old-provider','old-model','old-version','USD',NULL,NULL,'RESERVED',NULL)"
            )
        )
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as db:
            rows = db.execute(
                text(
                    "SELECT id, task_id, reserved_micros, status, decision_json FROM model_routing_requests"
                )
            ).all()
        assert rows == [("old-request", "old-task", None, "RESERVED", None)]
        assert BudgetLedger(sessionmaker(engine)).recent()[0]["cost_status"] == "UNKNOWN"
    finally:
        engine.dispose()


def test_whole_synthetic_profile_upgrade_preserves_all_domains_and_busy_rollback(tmp_path):
    import asyncio
    import sqlite3
    import pytest
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError
    from unified_skill_fixture import unified_host
    from scheduler_fixture import scheduled, run_to_completion
    from app.database.session import SessionLocal, engine as active
    from app.models import ManualMemory, MessageVoice, VoiceJob

    async def seed():
        async with unified_host(tmp_path / "seed") as h:
            _, result = await h.agent()
            with SessionLocal.begin() as db:
                db.add(
                    ManualMemory(
                        character_id="assistant",
                        owner_id="assistant",
                        content="Synthetic memory only",
                    )
                )
                db.add(
                    MessageVoice(
                        message_id=result["message_id"],
                        status="completed",
                        audio_path="synthetic.wav",
                        voice_profile="synthetic",
                    )
                )
                db.add(VoiceJob(message_id=result["message_id"], status="completed"))
            definition = scheduled(h.app, h.owner, h.clock)
            h.scheduler.save(definition)
            h.clock.advance(60)
            await h.scheduler.tick()
            row = h.scheduler.store.occurrences(h.owner)[0]
            await run_to_completion(h.app, h.scheduler, h.owner, row["occurrence_id"])
            await h.trace.flush()

    (tmp_path / "seed").mkdir()
    asyncio.run(seed())
    path = tmp_path / "legacy-copy.db"
    with active.connect() as connection, sqlite3.connect(path) as copied:
        connection.connection.driver_connection.backup(copied)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE model_routing_requests")
        db.commit()

    def content():
        with sqlite3.connect(path) as db:
            names = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'model_routing_requests'"
                )
            ]
            return {
                name: sorted(
                    (tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')), key=repr
                )
                for name in names
            }

    before = content()
    for name in (
        "messages",
        "manual_memories",
        "work_sessions",
        "agent_checkpoints",
        "agent_traces",
        "workflow_runs",
        "scheduled_occurrences",
        "message_voice",
        "voice_jobs",
    ):
        assert before[name], name
    target = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"timeout": 0.01})
    try:
        with sqlite3.connect(path) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with pytest.raises(OperationalError):
                migrate(target)
            lock.rollback()
        assert content() == before
        raised = []

        def interrupt(_conn, _cursor, statement, _parameters, _context, _many):
            if "ALTER TABLE model_routing_requests ADD COLUMN" in statement:
                raised.append(True)
                raise RuntimeError("Synthetic migration interruption")

        # Recreate the early additive table shape to interrupt an actual migration.
        with target.begin() as db:
            db.execute(
                text("CREATE TABLE IF NOT EXISTS model_routing_requests (id TEXT PRIMARY KEY)")
            )
        event.listen(target, "before_cursor_execute", interrupt)
        with pytest.raises(RuntimeError, match="Synthetic migration interruption"):
            migrate(target)
        event.remove(target, "before_cursor_execute", interrupt)
        assert raised and content() == before
        # The artificial partial ledger is empty; remove only this synthetic table.
        with target.begin() as db:
            db.execute(text("DROP TABLE model_routing_requests"))
        migrate(target)
        migrate(target)
        assert content() == before
        with target.connect() as db:
            assert db.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    finally:
        target.dispose()

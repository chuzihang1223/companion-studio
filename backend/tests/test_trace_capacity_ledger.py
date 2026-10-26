"""Independent SQLite fault injection for the durable admission capacity ledger.

Capacity is compared with actual source rows and BLOB byte lengths, rather than
with another copy of the ledger's implementation. All databases are test-owned.
"""

from contextlib import closing
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

import pytest

from app.services.trace.journal import TraceJournal, connect_journal


def command(*, event=True, padding=0):
    key = str(uuid4())
    return {
        "trace_id": key,
        "task_id": key,
        "event_type": "TASK_CREATED" if event else None,
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "metadata": {"synthetic_padding": "x" * padding},
    }


def ledger_matches_sources(journal):
    with journal.transaction() as db:
        counters = dict(
            db.execute(
                "SELECT key,CAST(value AS INTEGER) FROM meta "
                "WHERE key IN ('pending_commands','pending_payload_bytes')"
            )
        )
        count, size = db.execute(
            "SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) "
            "FROM commands WHERE state!='COMMITTED'"
        ).fetchone()
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert counters == {"pending_commands": count, "pending_payload_bytes": size}
    return count, size


def source_rows(journal):
    with journal.transaction() as db:
        return db.execute("SELECT * FROM commands ORDER BY sequence").fetchall()


def reject_counter_update(journal, key):
    assert key in {"pending_commands", "pending_payload_bytes"}
    with journal.transaction() as db:
        db.execute(
            "CREATE TRIGGER qa_ledger_abort BEFORE UPDATE ON meta "
            "WHEN NEW.key='" + key + "' BEGIN "
            "SELECT RAISE(ABORT,'controlled capacity ledger rejection'); END"
        )


def drop_rejection(journal):
    with journal.transaction() as db:
        db.execute("DROP TRIGGER qa_ledger_abort")


@pytest.mark.parametrize("counter", ["pending_commands", "pending_payload_bytes"])
def test_accept_and_capacity_counters_rollback_as_one_full_transaction(tmp_path, counter):
    path = tmp_path / "chat.db"
    with closing(TraceJournal(path, 8)) as journal:
        first = journal.accept("create", command())
        before, rows = journal.snapshot(), source_rows(journal)
        reject_counter_update(journal, counter)
        with pytest.raises(sqlite3.IntegrityError, match="controlled capacity ledger rejection"):
            journal.accept("create", command())
        assert journal.snapshot() == before and source_rows(journal) == rows
        assert ledger_matches_sources(journal)[0] == 1
        drop_rejection(journal)
    with closing(TraceJournal(path, 8)) as reopened:
        assert reopened.recover() == [first]
        assert ledger_matches_sources(reopened)[0] == 1
        reopened.accept("create", command())
        assert ledger_matches_sources(reopened)[0] == 2
        assert reopened.snapshot()["accepted"] == 2


@pytest.mark.parametrize("counter", ["pending_commands", "pending_payload_bytes"])
def test_batch_ack_and_capacity_release_rollback_together_then_ack_once(tmp_path, counter):
    path = tmp_path / "chat.db"
    with closing(TraceJournal(path, 8)) as journal:
        first = journal.accept("create", command())
        second = journal.accept("state", command(event=False))
        receipts = [(first[1]["_projection_id"], True), (second[1]["_projection_id"], False)]
        before, rows = journal.snapshot(), source_rows(journal)
        reject_counter_update(journal, counter)
        with pytest.raises(sqlite3.IntegrityError, match="controlled capacity ledger rejection"):
            journal.acknowledge(receipts)
        assert journal.snapshot() == before and source_rows(journal) == rows
        assert ledger_matches_sources(journal)[0] == 2
        drop_rejection(journal)
    with closing(TraceJournal(path, 8)) as reopened:
        assert ledger_matches_sources(reopened)[0] == 2
        assert reopened.acknowledge(receipts + receipts) == (2, 1)
        assert ledger_matches_sources(reopened) == (0, 0)
        assert reopened.acknowledge(receipts) == (0, 0)
        assert ledger_matches_sources(reopened) == (0, 0)
        value = reopened.snapshot()
        assert value["accepted"] == value["committed"] == 2
        assert value["accepted_events"] == value["committed_events"] == 1
    with closing(TraceJournal(path, 8)) as twice:
        assert ledger_matches_sources(twice) == (0, 0)
        assert twice.acknowledge(receipts) == (0, 0)


def test_failed_and_review_sources_keep_capacity_until_a_confirmed_ack(tmp_path):
    path = tmp_path / "chat.db"
    with closing(TraceJournal(path, 8)) as journal:
        items = [journal.accept("create", command()) for _ in range(8)]
        ids = [item[1]["_projection_id"] for item in items]
        full_size = ledger_matches_sources(journal)[1]
        assert journal.acknowledge([(ids[0], False)]) == (0, 0)
        journal.mark([ids[1]], "NEEDS_REVIEW", "SYNTHETIC_CASE")
        journal.failure([ids[2]], True)
        assert ledger_matches_sources(journal) == (8, full_size)
        before = source_rows(journal)
        with pytest.raises(ValueError, match="TRACE_JOURNAL_CAPACITY$"):
            journal.accept("create", command())
        assert source_rows(journal) == before
    with closing(TraceJournal(path, 8)) as reopened:
        states = reopened.snapshot()["states"]
        assert states["FAILED"] == states["NEEDS_REVIEW"] == states["RETRY_PENDING"] == 1
        assert ledger_matches_sources(reopened) == (8, full_size)
        with pytest.raises(ValueError, match="TRACE_JOURNAL_CAPACITY$"):
            reopened.accept("create", command())
        assert reopened.acknowledge([(ids[-1], True)]) == (1, 1)
        assert ledger_matches_sources(reopened)[0] == 7
        reopened.accept("create", command())
        assert ledger_matches_sources(reopened)[0] == 8
        assert reopened.snapshot()["accepted"] == 9
        assert reopened.snapshot()["committed"] == 1
        assert {"FAILED", "NEEDS_REVIEW"} <= reopened.snapshot()["states"].keys()


@pytest.mark.parametrize("old_values", ["missing", "stale"])
def test_open_rebuilds_missing_or_stale_capacity_from_preserved_sources(tmp_path, old_values):
    path = tmp_path / "chat.db"
    with closing(TraceJournal(path, 8)) as journal:
        items = [journal.accept("create", command()) for _ in range(3)]
        journal.acknowledge([(items[0][1]["_projection_id"], True)])
        journal.mark([items[1][1]["_projection_id"]], "NEEDS_REVIEW", "SYNTHETIC_CASE")
        before = source_rows(journal)
        expected = ledger_matches_sources(journal)
        journal_path = journal.path
    with closing(connect_journal(journal_path)) as raw:
        with raw:
            if old_values == "missing":
                raw.execute(
                    "DELETE FROM meta WHERE key IN ('pending_commands','pending_payload_bytes')"
                )
            else:
                raw.execute(
                    "UPDATE meta SET value='99999999' "
                    "WHERE key IN ('pending_commands','pending_payload_bytes')"
                )
    with closing(TraceJournal(path, 8)) as reopened:
        assert ledger_matches_sources(reopened) == expected
        assert source_rows(reopened) == before
        assert reopened.snapshot()["accepted"] == 3 and reopened.snapshot()["committed"] == 1
        assert len(reopened.recover()) == 1  # Review source is retained, never auto-replayed.
        assert ledger_matches_sources(reopened) == expected


def test_explicit_recovery_reconciles_stale_ledger_without_deleting_sources(tmp_path):
    with closing(TraceJournal(tmp_path / "chat.db", 8)) as journal:
        for _ in range(3):
            journal.accept("create", command())
        before = source_rows(journal)
        expected = ledger_matches_sources(journal)
        with journal.transaction() as db:
            db.execute(
                "UPDATE meta SET value='-1' "
                "WHERE key IN ('pending_commands','pending_payload_bytes')"
            )
        with pytest.raises(ValueError, match="TRACE_JOURNAL_CAPACITY_INTEGRITY"):
            journal.accept("create", command())
        assert len(journal.recover()) == 3
        assert ledger_matches_sources(journal) == expected
        assert source_rows(journal) == before


def test_non_ascii_json_is_accounted_as_actual_source_bytes(tmp_path):
    path = tmp_path / "chat.db"
    with closing(TraceJournal(path, 8)) as journal:
        data = command()
        data["metadata"] = {"synthetic_unicode": "中文🙂" * 100}
        item = journal.accept("create", data)
        count, size = ledger_matches_sources(journal)
        assert count == 1 and size > 1000
        source_id = item[1]["_projection_id"]
    with closing(TraceJournal(path, 8)) as reopened:
        assert ledger_matches_sources(reopened) == (count, size)
        assert reopened.acknowledge([(source_id, True)]) == (1, 1)
        assert ledger_matches_sources(reopened) == (0, 0)


def test_payload_byte_limit_is_enforced_and_ack_releases_exact_source_bytes(tmp_path):
    limit = 16 * 1024 * 1024
    with closing(TraceJournal(tmp_path / "chat.db", 2048)) as journal:
        ids = []
        for _ in range(700):
            try:
                item = journal.accept("create", command(padding=32_000))
            except ValueError as error:
                assert str(error) == "TRACE_JOURNAL_CAPACITY"
                break
            ids.append(item[1]["_projection_id"])
        else:
            pytest.fail("Actual large sources exceeded the original16MiB capacity")
        count, size = ledger_matches_sources(journal)
        assert count == len(ids) < 2048 and size <= limit
        with journal.transaction() as db:
            next_size = db.execute(
                "SELECT length(CAST(payload AS BLOB)) FROM commands LIMIT 1"
            ).fetchone()[0]
        assert size + next_size > limit
        assert journal.acknowledge([(key, True) for key in ids]) == (count, count)
        assert ledger_matches_sources(journal) == (0, 0)
        journal.accept("create", command())
        assert ledger_matches_sources(journal)[0] == 1

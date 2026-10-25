"""Actual FULL admission transactions, bounded recovery and owner protection."""

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from app.services.trace.journal import TraceJournal


def command():
    key = str(uuid4())
    return {
        "trace_id": key,
        "task_id": key,
        "event_type": "TASK_CREATED",
        "created_at": datetime.now(timezone.utc),
    }


def test_accepted_command_is_durable_before_the_projection_starts(tmp_path):
    path = tmp_path / "chat.db"
    journal = TraceJournal(path, 8)
    first = journal.accept("create", command())
    assert journal.snapshot()["accepted"] == 1
    journal.close()
    restored = TraceJournal(path, 8)
    try:
        recovered = restored.recover()
        assert recovered == [first]
        assert restored.snapshot()["pending"] == 1
        restored.acknowledge([(first[1]["_projection_id"], True)])
        restored.acknowledge([(first[1]["_projection_id"], True)])
        counts = restored.snapshot()
        assert counts["committed"] == counts["committed_events"] == 1
        assert counts["pending"] == 0 and restored.recover() == []
    finally:
        restored.close()


def test_pending_capacity_and_expiration_never_delete_an_accepted_source(tmp_path):
    journal = TraceJournal(tmp_path / "chat.db", 8)
    try:
        for _ in range(8):
            journal.accept("create", command())
        with pytest.raises(ValueError, match="CAPACITY"):
            journal.accept("create", command())
        with journal.transaction() as db:
            db.execute("UPDATE commands SET created=0")
        assert journal.recover() == []
        result = journal.snapshot()
        assert result["accepted"] == result["pending"] == 8
        assert result["states"] == {"NEEDS_REVIEW": 8}
    finally:
        journal.close()


def test_corrupt_payload_remains_explicitly_awaiting_review(tmp_path):
    journal = TraceJournal(tmp_path / "chat.db", 8)
    try:
        journal.accept("create", command())
        with journal.transaction() as db:
            db.execute("UPDATE commands SET payload='not the accepted payload'")
        assert journal.recover() == []
        assert journal.snapshot()["states"] == {"NEEDS_REVIEW": 1}
        assert journal.snapshot()["committed"] == 0
    finally:
        journal.close()


def test_only_one_projection_owner_and_relocation_is_not_silent(tmp_path):
    database = tmp_path / "chat.db"
    journal = TraceJournal(database, 8)
    try:
        with pytest.raises(OSError, match="OWNED"):
            TraceJournal(database, 8)
    finally:
        journal.close()
    with pytest.raises(ValueError, match="MISMATCH"):
        TraceJournal(tmp_path / "different.db", 8, journal.path.parent)


def test_journal_has_explicit_storage_and_permission_bounds(tmp_path):
    journal = TraceJournal(tmp_path / "chat.db", 8)
    try:
        with journal.transaction() as db:
            assert db.execute("PRAGMA max_page_count").fetchone()[0] == 8192
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if os.name == "nt":
            import win32api
            import win32con
            import win32security

            descriptor = win32security.GetNamedSecurityInfo(
                str(journal.path.parent),
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION,
            )
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
            )
            try:
                owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            finally:
                token.Close()
            allowed = {win32security.ConvertSidToStringSid(owner), "S-1-5-18"}
            # Windows can split one inheritable GENERIC_ALL ACE into effective
            # and inherit-only entries. Check actual principals/rights, not count.
            acl = descriptor.GetSecurityDescriptorDacl()
            entries = [acl.GetAce(i) for i in range(acl.GetAceCount())]
            assert {win32security.ConvertSidToStringSid(ace[2]) for ace in entries} == allowed
            assert all(ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE for ace in entries)
            assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
            child = win32security.GetNamedSecurityInfo(
                str(journal.path),
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION,
            )
            acl = child.GetSecurityDescriptorDacl()
            assert {
                win32security.ConvertSidToStringSid(acl.GetAce(i)[2])
                for i in range(acl.GetAceCount())
            } == allowed
        else:
            assert journal.path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        journal.close()

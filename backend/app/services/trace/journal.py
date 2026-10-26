"""Bounded independent SQLite admission log, not another Agent executor.

No journal transaction spans a main-database write/gate. Bounded WAL/FULL gives each
accepted command a crash-safe source without contending on chat.db's writer.
Main projection receipts resolve the commit-before-ACK uncertainty window.
"""

from contextlib import contextmanager
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from uuid import UUID, uuid4
from urllib.parse import quote

from app.core.filesystem import disk_path
from app.services.trace.command import decode, encode
from app.services.trace.private_files import JournalOwner, ordinary_path, private_directory


def connect_journal(path, *, read_only=False, **options):
    # SQLite's standard locking long-path VFS also supports its WAL/SHM
    # sidecars. Do not use a '*-none' VFS or change the machine's path policy.
    query = ["vfs=win32-longpath"] if os.name == "nt" else []
    if read_only:
        query.append("mode=ro")
    path = ordinary_path(path).resolve()
    # Even mode=ro can update SQLite's shared-memory sidecar. Validate all
    # candidate names BEFORE opening, including constructor/archive readers.
    for suffix in ("", "-journal", "-wal", "-shm"):
        ordinary_path(str(path) + suffix)
    # The '?' in Win32's extended prefix must be percent encoded; otherwise
    # SQLite parses it as URI authority/query and the VFS never sees the path.
    uri = "file:" + quote(str(disk_path(path)), safe=":") if os.name == "nt" else path.as_uri()
    if query:
        uri += "?" + "&".join(query)
    return sqlite3.connect(uri, uri=True, **options)


class TraceJournal:
    def __init__(self, database, capacity, directory=None):
        database = Path(database).resolve()
        directory = directory or database.parent / (database.name + ".trace-pending")
        private_directory(directory)
        self.path = ordinary_path(Path(directory) / "pending.sqlite3")
        self.owner = JournalOwner(Path(directory) / "owner.lock")
        self.lock, self.capacity = threading.RLock(), capacity
        self.database_digest = sha256(str(database).casefold().encode()).hexdigest()
        try:
            if disk_path(self.path).exists():
                # Reject an unrelated/corrupt file before any schema writes.
                check = connect_journal(self.path, read_only=True)
                try:
                    existing = dict(check.execute("SELECT key,value FROM meta"))
                    if (
                        existing.get("schema") != "1"
                        or existing.get("database") != self.database_digest
                    ):
                        raise ValueError("TRACE_JOURNAL_DATABASE_MISMATCH")
                finally:
                    check.close()
            with self.transaction() as db:
                db.execute("PRAGMA journal_mode=WAL")
                if db.execute("PRAGMA page_size").fetchone()[0] != 4096:
                    raise ValueError("TRACE_JOURNAL_PAGE_SIZE")
                db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
                db.execute("""CREATE TABLE IF NOT EXISTS commands (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    trace_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT,
                    state TEXT NOT NULL, event INTEGER NOT NULL, created REAL NOT NULL,
                    error_code TEXT, envelope_hash TEXT)""")
                if "envelope_hash" not in {r[1] for r in db.execute("PRAGMA table_info(commands)")}:
                    # Preserve an older unsealed source for explicit review;
                    # never manufacture an integrity seal for an unknown ID.
                    db.execute("ALTER TABLE commands ADD COLUMN envelope_hash TEXT")
                    db.execute("""UPDATE commands SET state='NEEDS_REVIEW',
                        error_code='JOURNAL_UNSEALED' WHERE state!='COMMITTED'""")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_pending_state ON commands(state, sequence)"
                )
                for key, value in {
                    "schema": "1",
                    "database": self.database_digest,
                    "stream": str(uuid4()),
                    "accepted": "0",
                    "accepted_events": "0",
                    "committed": "0",
                    "committed_events": "0",
                    "write_failures": "0",
                }.items():
                    db.execute("INSERT OR IGNORE INTO meta VALUES (?, ?)", (key, value))
                values = dict(db.execute("SELECT key, value FROM meta"))
                if values["database"] != self.database_digest:
                    raise ValueError("TRACE_JOURNAL_DATABASE_MISMATCH")
                self.stream = values["stream"]
                UUID(self.stream)
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("TRACE_JOURNAL_INTEGRITY")
                self._reconcile_capacity(db)
        except BaseException:
            if getattr(self, "connection", None):
                self.connection.close()
            self.owner.close()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            db = getattr(self, "connection", None)
            if db is None:
                for suffix in ("", "-journal", "-wal", "-shm"):
                    ordinary_path(str(self.path) + suffix)
                db = self.connection = connect_journal(
                    self.path, timeout=0.2, check_same_thread=False
                )
                # This connection is used sequentially only under self.lock;
                # SQLAlchemy projection Sessions are never shared across threads.
                db.execute("PRAGMA max_page_count=8192")
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA temp_store=MEMORY")
                db.execute("PRAGMA secure_delete=ON")
                db.execute("PRAGMA wal_autocheckpoint=512")
                db.execute("PRAGMA journal_size_limit=1048576")
            wal = Path(str(self.path) + "-wal")
            # The validated directory has an owner-only ACL and SQLite keeps
            # its open descriptors. No new path is opened per admission. Avoid
            # re-walking every ancestor for each SQL operation; inspect the
            # actual sidecar without following a replacement reparse point.
            try:
                wal_info = disk_path(wal).stat(follow_symlinks=False)
            except FileNotFoundError:
                wal_info = None
            if wal_info and (
                getattr(wal_info, "st_file_attributes", 0) & 0x400 or wal_info.st_nlink != 1
            ):
                raise OSError("TRACE_JOURNAL_LINK_REJECTED")
            if wal_info and wal_info.st_size >= 28 * 1024 * 1024:
                # Autocheckpoint alone is not a size cap if another process
                # holds a read snapshot. Never accept more in that condition.
                if db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise ValueError("TRACE_JOURNAL_WAL_CAPACITY")
            with db:
                yield db

    @staticmethod
    def add(db, key, amount):
        db.execute("UPDATE meta SET value=CAST(value AS INTEGER)+? WHERE key=?", (amount, key))

    @staticmethod
    def _reconcile_capacity(db):
        # One scan on open/recovery, not one increasingly large scan for each
        # admission. The hot-path counters change in the same FULL transaction
        # as the source row; an interrupted commit cannot split these values.
        count, size = db.execute(
            "SELECT count(*),coalesce(sum(length(payload)),0) FROM commands WHERE state!='COMMITTED'"
        ).fetchone()
        db.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [("pending_commands", str(count)), ("pending_payload_bytes", str(size))],
        )

    def accept(self, command, data):
        payload, digest = encode(command, data)
        key = str(uuid4())
        created = time.time()
        with self.transaction() as db:
            counters = {
                key: int(value)
                for key, value in db.execute(
                    "SELECT key,value FROM meta WHERE key IN ('pending_commands','pending_payload_bytes','accepted')"
                )
            }
            count, size = counters["pending_commands"], counters["pending_payload_bytes"]
            if count < 0 or size < 0:
                raise ValueError("TRACE_JOURNAL_CAPACITY_INTEGRITY")
            if count >= self.capacity or size + len(payload) > 16 * 1024 * 1024:
                raise ValueError("TRACE_JOURNAL_CAPACITY")
            # Only confirmed COMMITTED history is pruned; pending/failed sources
            # never disappear to make space. Counters remain in the atomic meta.
            accepted = counters["accepted"]
            if accepted % 256 == 0:
                db.execute("""DELETE FROM commands WHERE state='COMMITTED' AND sequence <
                    coalesce((SELECT sequence FROM commands WHERE state='COMMITTED'
                    ORDER BY sequence DESC LIMIT 1 OFFSET 65535), 0)""")
            cursor = db.execute(
                "INSERT INTO commands(id,trace_id,digest,payload,state,event,created) VALUES(?,?,?,?,?,?,?)",
                (
                    key,
                    data["trace_id"],
                    digest,
                    payload,
                    "QUEUED",
                    bool(data["event_type"]),
                    created,
                ),
            )
            sequence = cursor.lastrowid
            db.execute(
                "UPDATE commands SET envelope_hash=? WHERE id=?",
                (
                    self.seal(
                        key, sequence, data["trace_id"], bool(data["event_type"]), created, digest
                    ),
                    key,
                ),
            )
            db.execute(
                """UPDATE meta SET value=CAST(value AS INTEGER)+CASE key
                WHEN 'accepted' THEN 1 WHEN 'accepted_events' THEN ?
                WHEN 'pending_commands' THEN 1 ELSE ? END
                WHERE key IN ('accepted','accepted_events','pending_commands','pending_payload_bytes')""",
                (int(bool(data["event_type"])), len(payload)),
            )
        # The caller may return True only after the FULL transaction above exits.
        return self.envelope(command, data, key, sequence, digest)

    def envelope(self, command, data, key, sequence, digest):
        data = dict(data)
        data.update(
            _projection_id=key,
            _projection_sequence=sequence,
            _projection_stream=self.stream,
            _projection_digest=digest,
        )
        return command, data

    def seal(self, key, sequence, trace_id, event, created, digest):
        return sha256(
            json.dumps(
                [self.stream, key, sequence, trace_id, int(event), created, digest],
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()

    def recover(self):
        with self.transaction() as db:
            self._reconcile_capacity(db)
            # An expired source remains explicit and bounded, never silently
            # deleted or replayed outside the projection receipt's safety window.
            db.execute(
                """UPDATE commands SET state='NEEDS_REVIEW',error_code='JOURNAL_EXPIRED'
                WHERE state!='COMMITTED' AND created < ?""",
                (time.time() - 86400,),
            )
            rows = db.execute("""SELECT id,sequence,digest,payload,trace_id,event,created,envelope_hash FROM commands
                WHERE state IN ('QUEUED','PERSISTING','RETRY_PENDING') ORDER BY sequence""").fetchall()
        result = []
        for key, sequence, digest, payload, trace_id, event, created, seal in rows:
            try:
                UUID(key)
                if (
                    sequence <= 0
                    or event not in (0, 1)
                    or not math.isfinite(created)
                    or seal != self.seal(key, sequence, trace_id, event, created, digest)
                ):
                    raise ValueError("TRACE_JOURNAL_ENVELOPE")
                command, data = decode(payload, digest)
                if data["trace_id"] != trace_id or bool(data["event_type"]) != bool(event):
                    raise ValueError("TRACE_JOURNAL_SCOPE")
                result.append(self.envelope(command, data, key, sequence, digest))
            except (ValueError, TypeError, KeyError):
                self.mark([key], "NEEDS_REVIEW", "JOURNAL_INTEGRITY")
        return result

    def mark(self, ids, state, code=None):
        with self.transaction() as db:
            db.executemany(
                "UPDATE commands SET state=?,error_code=? WHERE id=? AND state!='COMMITTED'",
                [(state, code, key) for key in ids],
            )

    def failure(self, ids, retryable):
        with self.transaction() as db:
            db.executemany(
                "UPDATE commands SET state=?,error_code='PROJECTION_FAILED' WHERE id=? AND state!='COMMITTED'",
                [("RETRY_PENDING" if retryable else "NEEDS_REVIEW", key) for key in ids],
            )
            self.add(db, "write_failures", 1)

    def acknowledge(self, receipts):
        committed = events = released_bytes = 0
        with self.transaction() as db:
            for key, event_written in receipts:
                source = db.execute(
                    "SELECT event,length(payload) FROM commands WHERE id=? AND state!='COMMITTED'",
                    (key,),
                ).fetchone()
                if source and source[0] and not event_written:
                    # The pre-existing per-trace display cap remains enforced.
                    # Its explicit omission is FAILED, not a successful durable
                    # event. Keep the accepted source for review and flush=False.
                    db.execute(
                        "UPDATE commands SET state='FAILED',error_code='TRACE_EVENT_LIMIT' WHERE id=?",
                        (key,),
                    )
                    continue
                changed = db.execute(
                    """UPDATE commands SET state='COMMITTED',payload=NULL,error_code=NULL
                    WHERE id=? AND state!='COMMITTED'""",
                    (key,),
                ).rowcount
                committed += changed
                events += changed * int(event_written)
                released_bytes += changed * (source[1] or 0) if source else 0
            if committed:
                db.execute(
                    """UPDATE meta SET value=CAST(value AS INTEGER)+CASE key
                    WHEN 'committed' THEN ? WHEN 'committed_events' THEN ?
                    WHEN 'pending_commands' THEN ? ELSE ? END
                    WHERE key IN ('committed','committed_events','pending_commands','pending_payload_bytes')""",
                    (committed, events, -committed, -released_bytes),
                )
        return committed, events

    @staticmethod
    def archive(source_database, destination_database):
        """Preserve sanitized sources before an owned Evaluation staging cleanup.

        The worker must have exited (OS owner lock acquired). Both database
        locations are supplied by the existing UUID-scoped archive manager.
        Rebinding concerns the copied projection database only, never tools.
        """
        source_database, destination_database = Path(source_database), Path(destination_database)
        source = (
            source_database.parent / (source_database.name + ".trace-pending") / "pending.sqlite3"
        )
        if not disk_path(source).exists():
            return
        ordinary_path(source)
        owner = JournalOwner(source.parent / "owner.lock")
        try:
            source_db = connect_journal(source, read_only=True)
            try:
                expected = sha256(str(source_database.resolve()).casefold().encode()).hexdigest()
                if (
                    dict(source_db.execute("SELECT key,value FROM meta")).get("database")
                    != expected
                ):
                    raise ValueError("TRACE_JOURNAL_DATABASE_MISMATCH")
                directory = destination_database.parent / (
                    destination_database.name + ".trace-pending"
                )
                private_directory(directory)
                target = ordinary_path(directory / "pending.sqlite3")
                target_owner = JournalOwner(directory / "owner.lock")
                try:
                    destination = connect_journal(target)
                    try:
                        source_db.backup(destination)
                        destination.execute(
                            "UPDATE meta SET value=? WHERE key='database'",
                            (
                                sha256(
                                    str(destination_database.resolve()).casefold().encode()
                                ).hexdigest(),
                            ),
                        )
                        destination.commit()
                        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    finally:
                        destination.close()
                finally:
                    target_owner.close()
            finally:
                source_db.close()
        finally:
            owner.close()

    def snapshot(self):
        with self.lock:
            db = getattr(self, "connection", None)
            temporary = db is None
            if temporary:
                db = connect_journal(self.path, read_only=True)
            try:
                return self._snapshot(db)
            finally:
                if temporary:
                    db.close()

    def integrity(self, conversation_id, work_session_id=None):
        # Callers first verify the real conversation/session in the main DB.
        # No unprojected IDs, payloads or other-conversation counts are returned.
        with self.lock:
            query = "SELECT state,count(*) FROM commands WHERE state!='COMMITTED' AND json_extract(CASE WHEN json_valid(payload) THEN payload ELSE '{}' END,'$.data.conversation_id')=?"
            args = [conversation_id]
            if work_session_id:
                query += " AND json_extract(payload,'$.data.work_session_id')=?"
                args.append(work_session_id)
            states = dict(self.connection.execute(query + " GROUP BY state", args))
        return {
            "pending": sum(states.get(s, 0) for s in ("QUEUED", "PERSISTING", "RETRY_PENDING")),
            "needs_review": states.get("NEEDS_REVIEW", 0),
            "failed": states.get("FAILED", 0),
        }

    @staticmethod
    def _snapshot(db):
        result = {
            key: int(value)
            for key, value in db.execute(
                "SELECT key,value FROM meta WHERE key NOT IN ('database','stream','schema')"
            )
        }
        states = dict(db.execute("SELECT state,count(*) FROM commands GROUP BY state"))
        result["states"] = states
        result["pending"] = sum(value for key, value in states.items() if key != "COMMITTED")
        return result

    def close(self):
        with self.lock:
            connection = getattr(self, "connection", None)
            if connection:
                connection.close()
                self.connection = None
            self.owner.close()

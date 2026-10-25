"""Bounded, private-data-free observation of explicitly supplied SQLite engines.

No SQL or parameter text is retained. Dialect commit/rollback passthrough wrappers
record completion AFTER the real DBAPI call returns, unlike Engine's pre-commit
event. Context manager restores methods/listeners; no database value is changed.
Write lock duration is measured from successful DML/BEGIN IMMEDIATE while the
DBAPI connection reports in_transaction, not from a deferred SELECT transaction.
"""

from collections import Counter, deque
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import sys
import threading
import time
from uuid import UUID, uuid4

from sqlalchemy import event


def task_id():
    module = sys.modules.get("app.services.trace.scope")
    try:
        value = module.current_trace.get().task_id
        return str(UUID(str(value)))
    except (AttributeError, ValueError, TypeError):
        return None


def raw_connection(connection):
    raw = getattr(connection, "driver_connection", connection)
    if raw is connection:
        raw = getattr(connection, "dbapi_connection", connection)
    return raw


class Recorder:
    def __init__(self, engines, sample_limit=100000, tail_limit=512):
        self.lock = threading.RLock()
        self.started = time.perf_counter()
        self.sample_limit = sample_limit
        self.engines = {}
        self.connections = {}
        self.counters = Counter()
        self.tail = deque(maxlen=tail_limit)
        self.busy_intervals = deque(maxlen=128)
        self.transactions = []
        self.write_intervals = []
        self.statement_waits = []
        self.listeners, self.wrappers = [], []
        for label, engine in engines.items():
            if label not in {"business", "trace"} or engine.dialect.name != "sqlite":
                raise ValueError("observer accepts named SQLite engines only")
            database = str(Path(engine.url.database).resolve()).casefold()
            meta = {
                "engine": str(uuid4()),
                "role": label,
                "database_digest": sha256(database.encode()).hexdigest(),
            }
            self.engines[label] = meta
            self.install(engine, meta)

    def record(self, name, row, **values):
        self.counters[name] += 1
        self.tail.append(
            {
                "event": name,
                "elapsed_seconds": time.perf_counter() - self.started,
                **row["meta"],
                "connection": row["connection"],
                "transaction": row.get("transaction"),
                "thread": threading.get_ident(),
                "task_id": task_id(),
                **values,
            }
        )

    def get(self, raw, meta):
        identity = id(raw)
        row = self.connections.get(identity)
        if row is None:
            row = {
                "meta": meta,
                "connection": str(uuid4()),
                "checkedout": False,
                "transaction": None,
                "began": None,
                "write_acquired": None,
            }
            self.connections[identity] = row
        return row

    def append_sample(self, collection, value, kind):
        if len(collection) < self.sample_limit:
            collection.append(value)
        else:
            self.counters[kind + "_sample_overflow"] += 1

    def finish(self, raw, meta, kind, call_ms):
        with self.lock:
            row = self.get(raw, meta)
            now = time.perf_counter()
            values = {"dbapi_call_ms": call_ms}
            if row["began"] is not None:
                values["transaction_ms"] = (now - row["began"]) * 1000
                self.append_sample(self.transactions, values["transaction_ms"], "transaction")
            if row["write_acquired"] is not None:
                values["write_lock_ms"] = (now - row["write_acquired"]) * 1000
                self.append_sample(self.write_intervals, values["write_lock_ms"], "write_lock")
            self.record(kind, row, **values)
            row.update(transaction=None, began=None, write_acquired=None)

    def install(self, engine, meta):
        def listen(name, callback):
            event.listen(engine, name, callback)
            self.listeners.append((engine, name, callback))

        def checkout(raw, *_):
            with self.lock:
                row = self.get(raw, meta)
                row["checkedout"] = True
                self.record("checkout", row)

        def checkin(raw, *_):
            if raw is not None:
                with self.lock:
                    row = self.get(raw, meta)
                    row["checkedout"] = False
                    self.record("checkin", row)

        def closed(raw, *_):
            with self.lock:
                row = self.connections.get(id(raw))
                if row:
                    self.record("connection_closed", row)
                    self.connections.pop(id(raw), None)

        def begin(connection):
            with self.lock:
                row = self.get(connection.connection.driver_connection, meta)
                if row["transaction"] is None:
                    row.update(
                        transaction=str(uuid4()), began=time.perf_counter(), write_acquired=None
                    )
                    self.record("begin", row)

        def before(connection, cursor, statement, parameters, context, many):
            context._trace_observer_began = time.perf_counter()

        def after(connection, cursor, statement, parameters, context, many):
            with self.lock:
                now = time.perf_counter()
                raw = connection.connection.driver_connection
                row = self.get(raw, meta)
                elapsed = (now - context._trace_observer_began) * 1000
                self.append_sample(self.statement_waits, elapsed, "statement")
                text = statement.lstrip().upper()
                writes = (
                    getattr(context, "isinsert", False)
                    or getattr(context, "isupdate", False)
                    or getattr(context, "isdelete", False)
                    or text.startswith(
                        (
                            "INSERT ",
                            "UPDATE ",
                            "DELETE ",
                            "REPLACE ",
                            "BEGIN IMMEDIATE",
                            "BEGIN EXCLUSIVE",
                        )
                    )
                )
                if writes and getattr(raw, "in_transaction", False):
                    if row["write_acquired"] is None:
                        row["write_acquired"] = now
                        self.record("write_acquired", row, wait_ms=elapsed)

        def failure(context):
            with self.lock:
                connection = context.connection
                raw = connection.connection.driver_connection if connection else None
                row = self.get(raw, meta)
                error = context.original_exception
                code = getattr(error, "sqlite_errorcode", None)
                started = getattr(context.execution_context, "_trace_observer_began", None)
                wait = (time.perf_counter() - started) * 1000 if started is not None else None
                self.record(
                    "sqlite_error",
                    row,
                    sqlite_errorcode=code,
                    error_type=type(error).__name__,
                    wait_ms=wait,
                )
                if code is not None and code & 255 in (5, 6):
                    self.counters["sqlite_busy"] += 1
                    holders = [
                        {
                            **item["meta"],
                            "connection": item["connection"],
                            "transaction": item["transaction"],
                            "held_ms": (time.perf_counter() - item["write_acquired"]) * 1000,
                        }
                        for item in self.connections.values()
                        if item["write_acquired"] is not None
                        and item["meta"]["database_digest"] == meta["database_digest"]
                    ]
                    self.busy_intervals.append(
                        {
                            "elapsed_seconds": time.perf_counter() - self.started,
                            "sqlite_errorcode": code,
                            "wait_ms": wait,
                            "observed_holders": holders,
                        }
                    )

        for name, callback in (
            ("checkout", checkout),
            ("checkin", checkin),
            ("close", closed),
            ("begin", begin),
            ("before_cursor_execute", before),
            ("after_cursor_execute", after),
            ("handle_error", failure),
        ):
            listen(name, callback)

        # Each Engine owns its dialect instance. Passthrough does not issue SQL.
        for name, kind in (("do_commit", "commit_complete"), ("do_rollback", "rollback_complete")):
            original = getattr(engine.dialect, name)

            def wrapper(connection, _original=original, _kind=kind):
                began = time.perf_counter()
                result = _original(connection)
                self.finish(
                    raw_connection(connection), meta, _kind, (time.perf_counter() - began) * 1000
                )
                return result

            setattr(engine.dialect, name, wrapper)
            self.wrappers.append((engine.dialect, name, original, wrapper))

    @staticmethod
    def distribution(values):
        ordered = sorted(values)
        return {
            "count": len(values),
            "maximum_ms": ordered[-1] if ordered else None,
            "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else None,
        }

    def snapshot(self):
        with self.lock:
            return {
                "method": "bounded event listeners plus post-DBAPI commit/rollback passthrough",
                "limitations": "observed named engines only; external-process holders are unknown; "
                "begin duration is logical transaction, write-lock duration starts after actual write acquisition",
                "engine_count": len(self.engines),
                "engines": self.engines,
                "counters": dict(self.counters),
                "checked_out": sum(row["checkedout"] for row in self.connections.values()),
                "active_write_holders": sum(
                    row["write_acquired"] is not None for row in self.connections.values()
                ),
                "transaction": self.distribution(self.transactions),
                "write_lock": self.distribution(self.write_intervals),
                "statement_wait": self.distribution(self.statement_waits),
                "duration_samples_ms": list(self.transactions),
                "tail": list(self.tail),
                "busy_holder_intervals": list(self.busy_intervals),
            }

    def close(self):
        for engine, name, callback in self.listeners:
            event.remove(engine, name, callback)
        for dialect, name, original, wrapper in self.wrappers:
            if getattr(dialect, name) is wrapper:
                setattr(dialect, name, original)
        self.listeners.clear()
        self.wrappers.clear()


@contextmanager
def observe(engines, **limits):
    recorder = Recorder(engines, **limits)
    try:
        yield recorder
    finally:
        recorder.close()

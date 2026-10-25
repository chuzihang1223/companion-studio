"""Fair in-process SQLite writers; readers and external processes remain SQLite's job.

Only registered engines participate. A lease starts at the first Session write,
not its first SELECT, and ends after the real outer transaction closes. Internal
flush transactions/savepoints must never release another writer prematurely.
"""

from collections import deque
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
import threading
import time
from weakref import WeakKeyDictionary, WeakValueDictionary

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


write_deadline = ContextVar("sqlite_write_deadline", default=None)
_engines = WeakKeyDictionary()
_gates = WeakValueDictionary()
_registry_lock = threading.Lock()


class WriteGateTimeout(OperationalError):
    def __init__(self):
        # Not a fabricated SQLite BUSY: metrics distinguish coordination waits.
        super().__init__(None, None, TimeoutError("SQLITE_WRITE_GATE_TIMEOUT"))


class WriteGate:
    def __init__(self, identity):
        self.identity = sha256(identity.encode()).hexdigest()
        self.condition = threading.Condition()
        self.waiters = deque()
        self.owner = None
        self.owner_thread = None
        self.wait_ms = self.max_wait_ms = 0.0
        self.transactions = self.peak_waiters = 0

    def acquire(self, token, deadline):
        began = time.perf_counter()
        ticket = object()
        with self.condition:
            if self.owner is token:
                return
            # Waiting for another connection owned by this same thread could
            # never progress. Fail explicitly instead of creating a Python lock
            # deadlock in code which SQLite would also reject as a nested writer.
            if self.owner_thread == threading.get_ident() or len(self.waiters) >= 128:
                raise WriteGateTimeout()
            self.waiters.append(ticket)
            self.peak_waiters = max(self.peak_waiters, len(self.waiters))
            try:
                while self.owner is not None or self.waiters[0] is not ticket:
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        raise WriteGateTimeout()
                    self.condition.wait(remaining)
                if time.perf_counter() >= deadline:
                    raise WriteGateTimeout()
                self.waiters.popleft()
                self.owner, self.owner_thread = token, threading.get_ident()
                elapsed = (time.perf_counter() - began) * 1000
                self.wait_ms += elapsed
                self.max_wait_ms = max(self.max_wait_ms, elapsed)
                self.transactions += 1
            except BaseException:
                if ticket in self.waiters:
                    self.waiters.remove(ticket)
                self.condition.notify_all()
                raise

    def release(self, token):
        with self.condition:
            if self.owner is token:
                self.owner = self.owner_thread = None
                self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return {
                "database_digest": self.identity,
                "waiting": len(self.waiters),
                "owned": self.owner is not None,
                "transactions": self.transactions,
                "peak_waiters": self.peak_waiters,
                "wait_ms": self.wait_ms,
                "max_wait_ms": self.max_wait_ms,
            }


def register_write_engine(engine, timeout=8):
    if engine.dialect.name != "sqlite" or engine.url.database in {None, ":memory:"}:
        return None
    identity = str(Path(engine.url.database).resolve()).casefold()
    with _registry_lock:
        gate = _gates.get(identity)
        if gate is None:
            gate = WriteGate(identity)
            _gates[identity] = gate
        if engine not in _engines:
            _engines[engine] = (gate, timeout)
        return gate


def claim_write(session):
    if "companion_write_lease" in session.info:
        return
    with _registry_lock:
        entry = _engines.get(session.get_bind())
    if entry is None:
        return
    gate, timeout = entry
    # Establish the root SessionTransaction first, so close/rollback can always
    # release a lease even if BEGIN IMMEDIATE or the following flush fails.
    session.connection()
    token = object()
    deadline = write_deadline.get() or time.perf_counter() + timeout
    gate.acquire(token, deadline)
    session.info["companion_write_lease"] = (gate, token)


@event.listens_for(Session, "before_flush")
def before_flush(session, *_):
    if session.new or session.dirty or session.deleted:
        claim_write(session)


@event.listens_for(Session, "do_orm_execute")
def before_execute(state):
    statement = state.statement
    text = getattr(statement, "text", "").lstrip().upper()
    if (
        state.is_insert
        or state.is_update
        or state.is_delete
        or text.startswith(
            ("BEGIN IMMEDIATE", "BEGIN EXCLUSIVE", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ")
        )
    ):
        claim_write(state.session)


@event.listens_for(Session, "after_transaction_end")
def after_transaction_end(session, transaction):
    if transaction.parent is None:
        lease = session.info.pop("companion_write_lease", None)
        if lease:
            lease[0].release(lease[1])

"""Independent Trace integrity fixtures. Only owned databases/processes are used."""

import asyncio
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.filesystem import disk_path
from app.database.migrations import migrate
from app.models import Conversation, WorkSession
from app.models.agent_trace import AgentTrace, TraceEvent
from app.services.trace.store import TraceStore


class TraceCase:
    def __init__(self, root, filename="trace.db"):
        self.root, self.path = root, root / filename
        self.engines = [
            create_engine(
                f"sqlite:///{self.path.as_posix()}",
                connect_args={"check_same_thread": False, "timeout": 0.03},
            )
            for _ in range(2)
        ]
        for engine in self.engines:
            event.listen(engine, "connect", lambda db, _: db.execute("PRAGMA journal_mode=WAL"))
        self.engine, self.business = self.engines
        migrate(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.business_factory = sessionmaker(self.business, expire_on_commit=False)
        self.stores, self.blockers, self.releases = [], [], []
        self.owner, self.session = str(uuid4()), str(uuid4())
        with self.factory.begin() as db:
            db.add(Conversation(id=self.owner, character_id="assistant", conversation_type="work"))
            db.add(WorkSession(id=self.session, conversation_id=self.owner, goal="QA synthetic"))

    def store(self, **options):
        value = TraceStore(self.factory, **options)
        self.stores.append(value)
        return value

    def create(self, store, key=None, **options):
        key = key or str(uuid4())
        assert store.enqueue(
            "create",
            {
                "trace_id": key,
                "task_id": key,
                "conversation_id": self.owner,
                "event_type": "TASK_CREATED",
                **options,
            },
        )
        return key

    def hold(self):
        value = sqlite3.connect(self.path)
        value.execute("BEGIN IMMEDIATE")
        self.blockers.append(value)
        return value

    def release_all(self):
        for release in self.releases:
            release.set()
        for blocker in self.blockers:
            try:
                blocker.rollback()
                blocker.close()
            except sqlite3.ProgrammingError:
                pass
        self.blockers.clear()

    def count(self, model, **where):
        with self.factory() as db:
            return db.scalar(select(func.count()).select_from(model).filter_by(**where))

    def dispose(self):
        for engine in self.engines:
            assert engine.pool.checkedout() == 0
            engine.dispose()


def exercise(case, scenario):
    async def owned():
        try:
            await scenario()
        finally:
            case.release_all()
            for store in reversed(case.stores):
                if not store.closed:
                    await store.stop()

    try:
        asyncio.run(owned())
    finally:
        case.dispose()


async def until(predicate, seconds=4):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.001)
    assert predicate(), "Controlled condition did not occur before its bounded deadline"


def balanced(store):
    value = store.journal.snapshot()
    assert value["pending"] == 0
    assert value["accepted"] == value["committed"]
    assert store.metrics["dropped"] == 0
    return value


def receipt_rows(case, trace):
    with case.factory() as db:
        return db.execute(
            text(
                "SELECT id,trace_id,stream_id,sequence,payload_hash,event_written,event_id "
                "FROM trace_projection_receipts WHERE trace_id=:trace ORDER BY sequence"
            ),
            {"trace": trace},
        ).all()


CHILD = r"""import asyncio,json,os,sys,threading,time
from pathlib import Path
root,folder,mode,key=Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3],sys.argv[4]
os.environ.update(COMPANION_DATA_DIR=str(folder/'profile'),COMPANION_CLEAN_DISTRIBUTION='1',COMPANION_DEFAULT_CHARACTER='assistant',OPENAI_API_KEY='',OPENAI_MODEL='',OPENAI_BASE_URL='http://127.0.0.1:9/v1',FISH_API_KEY='',FISH_REFERENCE_ID='',VOICE_PREFETCH_ENABLED='false',VOICE_MODEL_DIR=str(folder/'no-model'),VOICE_INDEX_MODEL_DIR=str(folder/'no-model'),VOICE_REFERENCES_DIR=str(folder/'no-references'))
sys.path.insert(0,str(root/'backend'))
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.migrations import migrate
from app.services.trace.store import TraceStore
engine=create_engine('sqlite:///'+(folder/'trace.db').as_posix(),connect_args={'check_same_thread':False,'timeout':.03})
migrate(engine)
store=TraceStore(sessionmaker(engine,expire_on_commit=False))
assert store.enqueue('create',{'trace_id':key,'task_id':key,'event_type':'TASK_CREATED'})
assert store.enqueue('finish',{'trace_id':key,'task_id':key,'status':'completed','event_type':'TASK_COMPLETED'})
print(json.dumps({'accepted':store.journal.snapshot()['accepted']}),flush=True)
if mode=='queued':os._exit(73)
if mode=='committed':
 store._write(list(store.queue.queue))
 os._exit(73)
async def run():
 if mode=='loop':
  store.start()
  return
 if mode=='executor':
  release=threading.Event();executor=store.executor
  executor.submit(release.wait,5)
  store.start()
  end=time.monotonic()+3
  while executor._work_queue.qsize()==0 and time.monotonic()<end:await asyncio.sleep(.001)
  assert executor._work_queue.qsize()>0
  executor.shutdown(wait=False,cancel_futures=True)
  release.set()
  assert not await store.flush(timeout=.3)
  await store.stop()
asyncio.run(run())
os._exit(0)
"""


def crash_child(root, mode):
    folder = root / ("child-" + mode)
    folder.mkdir()
    script, key = folder / "owned_probe.py", str(uuid4())
    script.write_text(CHILD, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(script),
            str(Path(__file__).resolve().parents[2]),
            str(folder),
            mode,
            key,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=12,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == (73 if mode in {"queued", "committed"} else 0), result.stderr
    assert json.loads(result.stdout.splitlines()[0])["accepted"] == 2
    return folder, key


def recover_child(folder, key):
    engine = create_engine(f"sqlite:///{(folder / 'trace.db').as_posix()}")
    factory = sessionmaker(engine, expire_on_commit=False)
    store = TraceStore(factory)

    async def run():
        try:
            store.recover()
            store.start()
            assert await store.flush()
            value = balanced(store)
            assert value["accepted"] == value["committed"] == 2
            with factory() as db:
                row = db.get(AgentTrace, key)
                assert row.status == "completed"
                names = list(
                    db.scalars(select(TraceEvent.event_type).where(TraceEvent.trace_id == key))
                )
                assert Counter(names) == {"TASK_CREATED": 1, "TASK_COMPLETED": 1}
        finally:
            await store.stop()

    try:
        asyncio.run(run())
    finally:
        assert engine.pool.checkedout() == 0
        engine.dispose()


def file_snapshot(root):
    # Test every archived file even when the intentionally long Windows target
    # requires extended IO paths. Missing a journal would weaken read-only QA.
    physical_root = disk_path(root.resolve())
    return {
        p.relative_to(physical_root).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in physical_root.rglob("*")
        if p.is_file()
    }


async def runtime_case(root, kind):
    """Original services with explicitly synthetic providers/desktop/PCM."""
    from unified_skill_fixture import unified_host
    from workflow_fixture import graph, start, settle
    from unified_skill_scenarios import scheduled_run, complete_schedule

    async with unified_host(root) as h:
        if kind == "agent":
            plan, result = await h.agent()
            assert len(result["tools"]) == 1
            rows = h.app.state.trace_repository.listing(h.owner, task_id=plan["plan_id"])["items"]
            assert len(rows) == 1 and rows[0]["status"] == "completed"
        elif kind == "workflow":
            key = start(h.app, h.owner, graph())
            result = await settle(h.app, h.owner, key)
            assert result["state"] == "COMPLETED" and result["output"]["finish"] == "Hello Studio"
            assert result["trace_id"]
            trace = h.app.state.trace_repository.detail(result["trace_id"], h.owner)
            assert trace["task_id"] == result["task_id"]
        elif kind == "mcp":
            from app.services.evaluation.router_integration_cases import mcp

            checks = await mcp(h.fixture, h.workspace)
            assert checks and all(checks.values()), checks
            assert h.fixture.observation["tool_calls"] == 1
            await h.app.state.mcp_connections.close()
            assert not h.app.state.mcp_connections.connections
        elif kind == "scheduler":
            occurrence = await scheduled_run(h, graph())
            final, result, _ = await complete_schedule(h, occurrence)
            assert final["status"] == result["state"] == "COMPLETED"
            assert result["trace_id"]
            trace = h.app.state.trace_repository.detail(result["trace_id"], h.owner)
            assert trace["task_id"] == result["task_id"]
        elif kind == "computer":
            from app.services.workflows.contracts import Node

            target, observed = str(uuid4()), str(uuid4())
            calls = []

            async def request(operation, arguments, session_id, timeout=60):
                calls.append(arguments)
                return {
                    "status": "SUCCESS",
                    "verified": True,
                    "executed": True,
                    "window_id": target,
                    "observation_id": observed,
                    "element_count": 2,
                    "duration_ms": 2,
                    "raw_tree": "SYNTHETIC_RAW_TREE_CANARY",
                }

            h.app.state.desktop_bridge = SimpleNamespace(request=request)
            node = Node(
                node_id="observe",
                type="computer_use",
                config={"action": "observe", "arguments": {"target_id": target}},
            )
            context, _ = h.app.state.workflows.bridge.prepare(
                {"conversation_id": h.owner, "renderer_id": "trace-qa"},
                node,
                {"target_id": target},
                None,
            )
            with await h.app.state.trace_recorder.execution_scope(
                context.task_id, h.owner, context.work_session_id
            ):
                result, _ = await h.app.state.workflows.bridge.execute(
                    context, {context.calls[0]["id"]}
                )
            assert len(calls) == 1 and calls[0]["owner"]["conversation_id"] == h.owner
            assert "SYNTHETIC_RAW_TREE_CANARY" not in json.dumps(result)
            assert (
                h.app.state.checkpoint_manager.detail(context.task_id, h.owner)["checkpoints"][0][
                    "step_state"
                ]
                == "COMMITTED"
            )
        elif kind == "voice":
            from app.services.realtime_voice.contracts import Frame, Scope, StartRequest
            from app.services.evaluation.voice_cases import signal

            manager = h.app.state.realtime_voice
            options = StartRequest(
                owner_id=uuid4(), conversation_id=h.owner, character_id="assistant"
            )
            identity = await manager.start(options)
            identity = await manager.begin(
                Scope(owner_id=options.owner_id, voice_session_id=identity["voice_session_id"])
            )
            scope = Scope.model_validate({k: identity[k] for k in Scope.model_fields})
            for sequence in range(3):
                manager.frame(Frame(**scope.model_dump(), **signal(sequence), timestamp=0))
            assert (await manager.finalize(scope))["kind"] == "FINAL"
            await manager.interrupt(scope)
            await manager.stop_owner(options.owner_id)
            assert not manager.rows
            await h.trace.flush()
            with h.trace.factory() as db:
                trace = db.scalar(
                    select(AgentTrace).where(AgentTrace.task_id == str(scope.turn_id))
                )
                assert trace and trace.conversation_id == h.owner and trace.status == "cancelled"
        else:
            raise AssertionError("Unknown controlled scenario")
        assert await h.trace.flush()
        balanced(h.trace)
        assert not h.app.state.runtime_registry.active

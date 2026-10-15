import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.database.migrations import migrate
from app.models import AgentCheckpoint, Conversation, Message, RecoveryTask, WorkSession
from app.services.agent_tools import ToolExecutor
from app.services.recovery.checkpoints import CheckpointManager, RecoveryError
from app.services.recovery.reconciliation import StateReconciler, resource_snapshot


@pytest.fixture
def ledger(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'ledger.db').as_posix()}")
    migrate(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        conversation = Conversation(character_id="assistant", conversation_type="work")
        db.add(conversation)
        db.flush()
        session = WorkSession(conversation_id=conversation.id, goal="synthetic goal")
        db.add(session)
        db.commit()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = SimpleNamespace(
        task_id=str(uuid4()),
        recovery_task_id=None,
        conversation_id=conversation.id,
        work_session_id=session.id,
        renderer_id="test-renderer",
        coding_mode="write_workspace",
        calls=[
            {
                "id": "file-operation",
                "name": "code.create_file",
                "arguments": {"path": "result.md", "content": "fixture\n"},
            }
        ],
        executor=ToolExecutor(workspace),
    )
    work = SimpleNamespace(snapshot=lambda _id: {"workspace_root": str(workspace)})
    manager = CheckpointManager(factory)
    root = manager.begin(context, work)
    app = SimpleNamespace(state=SimpleNamespace(work_state=work))
    yield manager, context, app, root, factory
    engine.dispose()


def test_checkpoint_intent_states_unique_operation_and_repeated_migration(ledger):
    manager, context, _, root, factory = ledger
    call = context.calls[0]
    step = manager.prepare(root, call, 0, resource_snapshot(context, call))
    assert manager.prepare(root, call, 0, resource_snapshot(context, call)) == step
    for state in ("EXECUTING", "OBSERVED", "VERIFIED", "COMMITTED"):
        manager.step(step, state, result={"verified": True, "content": "private body"})
    detail = manager.detail(root, context.conversation_id)
    assert detail["checkpoints"][0]["step_state"] == "COMMITTED"
    assert "private body" not in str(detail)
    with pytest.raises(RecoveryError, match="SIDE_EFFECT_UNCERTAIN"):
        manager.prepare(root, call, 0, {})
    migrate(factory.kw["bind"])
    migrate(factory.kw["bind"])
    with factory() as db:
        assert (
            db.scalar(
                text(
                    "SELECT version FROM studio_schema_migrations WHERE component='agent_recovery'"
                )
            )
            == 1
        )
        assert len(list(db.scalars(select(AgentCheckpoint)))) == 1


def test_write_completed_before_crash_is_verified_without_another_write(ledger):
    manager, context, app, root, _ = ledger
    step = manager.prepare(root, context.calls[0], 0, resource_snapshot(context, context.calls[0]))
    manager.step(step, "EXECUTING")
    context.executor.execute(context.calls[0])  # actual file write, response/checkpoint lost
    CheckpointManager(manager.factory).recover()
    report = asyncio.run(StateReconciler(app, manager).inspect(root, context))
    assert report["can_resume"] and report["items"][0]["action"] == "SKIP_VERIFIED"
    assert manager.detail(root, context.conversation_id)["state"] == "INTERRUPTED"
    assert len(list(Path(context.executor.workspace).iterdir())) == 1


def test_conflicts_unknown_effects_corrupt_schema_and_ownership(ledger):
    manager, context, app, root, factory = ledger
    step = manager.prepare(root, context.calls[0], 0, resource_snapshot(context, context.calls[0]))
    manager.step(step, "EXECUTING")
    (context.executor.workspace / "result.md").write_text("externally changed", encoding="utf-8")
    preview = asyncio.run(StateReconciler(app, manager).inspect(root, context))
    assert not preview["can_resume"] and preview["items"][0]["error_code"] == "FILE_STATE_CHANGED"
    app.state.work_state.snapshot = lambda _: {"workspace_root": "different workspace"}
    with pytest.raises(RecoveryError, match="CHECKPOINT_CONFLICT"):
        asyncio.run(StateReconciler(app, manager).inspect(root, context))
    with pytest.raises(RecoveryError, match="RECOVERY_NOT_FOUND"):
        manager.detail(root, str(uuid4()))
    with factory() as db:
        db.get(AgentCheckpoint, step).schema_version = 99
        db.commit()
    with pytest.raises(RecoveryError, match="CHECKPOINT_INVALID"):
        manager.detail(root, context.conversation_id)


def test_resume_claim_is_cas_guarded_and_request_deduplicated(ledger):
    manager, context, _, root, _ = ledger
    manager.recover()
    detail = manager.detail(root, context.conversation_id)
    request_id = str(uuid4())
    lease, execution = manager.claim(
        root, context.conversation_id, context.work_session_id, request_id, detail["revision"]
    )
    assert execution != root
    assert (
        manager.claim(
            root, context.conversation_id, context.work_session_id, request_id, detail["revision"]
        )
        is None
    )
    with pytest.raises(RecoveryError):
        manager.claim(
            root, context.conversation_id, context.work_session_id, str(uuid4()), detail["revision"]
        )
    manager.release(root, "different lease")
    with pytest.raises(RecoveryError):
        manager.claim(
            root, context.conversation_id, context.work_session_id, str(uuid4()), detail["revision"]
        )
    manager.release(root, lease)


def test_cancelled_tasks_cannot_resume_and_pruning_retains_chat(ledger):
    manager, context, _, root, factory = ledger
    manager.state(root, "CANCELLED")
    with factory() as db:
        db.get(RecoveryTask, root).updated_at = datetime.now(timezone.utc) - timedelta(days=40)
        db.add(
            Message(
                character_id="assistant",
                conversation_id=context.conversation_id,
                role="user",
                content="keep",
            )
        )
        db.commit()
    with pytest.raises(RecoveryError):
        manager.claim(root, context.conversation_id, context.work_session_id, str(uuid4()), 2)
    assert manager.prune() == 1
    with factory() as db:
        assert db.get(WorkSession, context.work_session_id)
        assert db.scalar(select(Message)).content == "keep"


def test_transaction_failure_does_not_create_execution_intent(ledger, monkeypatch):
    manager, context, _, root, factory = ledger
    session_type = factory.class_
    original = session_type.commit

    def fail(_self):
        raise OperationalError("controlled commit", {}, Exception())

    monkeypatch.setattr(session_type, "commit", fail)
    with pytest.raises(OperationalError):
        manager.prepare(root, context.calls[0], 0, resource_snapshot(context, context.calls[0]))
    monkeypatch.setattr(session_type, "commit", original)
    assert manager.detail(root, context.conversation_id)["checkpoints"] == []
    assert not (context.executor.workspace / "result.md").exists()

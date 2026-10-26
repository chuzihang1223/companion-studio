"""Opt-in UI fixture: preserve one review-needed source in an owned test profile."""

from pathlib import Path
import os
import sys
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def main():
    path = Path(sys.argv[1])
    root = Path(__file__).resolve().parents[2] / ".cache"
    if (
        path.name != "chat.db"
        or path.parent.name != "UserData"
        or not path.resolve().is_relative_to(root)
    ):
        raise ValueError("Only the isolated UI fixture profile can be seeded")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError("Fixture links are not allowed")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError("An existing, unlinked fixture database is required")
    if Path(os.environ.get("COMPANION_DATA_DIR", "")).resolve() != path.parent.resolve():
        raise ValueError("Fixture data environment must match its validated database")
    trace_id, task_id, owner = map(lambda v: str(UUID(v)), sys.argv[2:5])
    # Settings import may create the data directory. Authorize its location
    # before importing any application module, even when used outside the UI.
    from app.services.trace.store import TraceStore

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    store = TraceStore(sessionmaker(engine, expire_on_commit=False))
    try:
        assert store.journal.snapshot()["pending"] == 0
        assert store.enqueue(
            "event",
            {
                "trace_id": trace_id,
                "task_id": task_id,
                "conversation_id": owner,
                "event_type": "OBSERVATION_CREATED",
                "metadata": {"verified": False},
            },
        )
        item = store.journal.recover()[0]
        store.journal.mark([item[1]["_projection_id"]], "NEEDS_REVIEW", "SYNTHETIC_UI_CASE")
        assert store.journal.snapshot()["states"]["NEEDS_REVIEW"] == 1
    finally:
        store.close_idle()
        engine.dispose()


if __name__ == "__main__":
    main()

"""Bounded metadata-only watches over the conversation's currently selected project."""

from datetime import timedelta
from contextlib import ExitStack
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from sqlalchemy import select

from app.models.schedules import SchedulerSourceRow
from app.services.knowledge.filesystem import checked_path, no_links, root_record
from app.services.recovery.checkpoints import digest, encoded
from app.services.scheduling.contracts import ScheduleError
from app.services.scheduling.time_rules import stamp, utc
from app.services.scheduling.directory_pin import pin_directory
from app.services.workspace_paths import private_path, scoped_path


def scope_identity(workspace):
    if not workspace:
        return None
    row = root_record(workspace)
    return {key: value for key, value in row.items() if key != "id"}


def metadata_snapshot(root):
    with ExitStack() as pins:
        return pinned_snapshot(root, pins)


def pinned_snapshot(root, pins):
    """No document contents or filenames enter the journal. Incomplete scans are errors."""
    checked_path(root, ".")
    result, pending, examined = {}, [(Path(root["path"]), 0)], 0
    pins.enter_context(pin_directory(Path(root["path"])))
    while pending:
        folder, depth = pending.pop()
        no_links(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                examined += 1
                if examined > 1000:
                    raise ScheduleError("SCHEDULE_WATCH_ENTRY_LIMIT")
                if private_path(Path(entry.path)):
                    continue
                scoped_path(
                    Path(root["path"]), Path(entry.path).relative_to(root["path"]).as_posix()
                )
                no_links(Path(entry.path))
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ScheduleError("SCHEDULE_WATCH_REPARSE_POINT")
                name = entry.name.lower()
                if name in {".git", "node_modules", ".venv", ".cache", "__pycache__"}:
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if depth >= 4:
                        raise ScheduleError("SCHEDULE_WATCH_DEPTH_LIMIT")
                    pins.enter_context(pin_directory(Path(entry.path)))
                    pending.append((Path(entry.path), depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    if name.startswith(("~", ".~")) or name.endswith(
                        (".tmp", ".temp", ".swp", ".lock", "~")
                    ):
                        continue
                    relative = Path(entry.path).relative_to(root["path"]).as_posix()
                    result[digest(relative)] = [
                        info.st_size,
                        str(info.st_mtime_ns),
                        str(info.st_ino),
                    ]
    checked_path(root, ".")
    return digest(result)


class ConditionWatch:
    def __init__(self, app, store):
        self.app, self.store = app, store

    def register(self, owner, kind, now, workflow_run_id=None):
        workspace = self.app.state.workflows.scope(owner)
        body = {"enabled": True, "expires_at": stamp(now + timedelta(hours=24))}
        if kind == "FILE_CHANGE":
            body["root"] = scope_identity(workspace)
            if not body["root"]:
                raise ScheduleError("SCHEDULE_SELECTED_WORKSPACE_REQUIRED")
            metadata_snapshot(body["root"])
        elif kind == "WORKFLOW_STATE" and workflow_run_id:
            self.app.state.workflows.repository.run(workflow_run_id, owner)
            body["workflow_run_id"] = workflow_run_id
        else:
            raise ScheduleError("SCHEDULE_SOURCE_INVALID")
        key = str(uuid4())
        with self.store.transaction() as db:
            db.add(SchedulerSourceRow(id=key, owner_id=owner, kind=kind, body=encoded(body)))
        return {"source_id": key, "condition_type": kind, "expires_at": body["expires_at"]}

    def source(self, key, owner, now):
        with self.store.factory() as db:
            row = db.get(SchedulerSourceRow, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_SOURCE_NOT_FOUND", 404)
            body, kind = json.loads(row.body), row.kind
        if not body["enabled"] or utc(body["expires_at"]) <= now:
            raise ScheduleError("SCHEDULE_SOURCE_REVOKED")
        if (
            kind == "FILE_CHANGE"
            and scope_identity(self.app.state.workflows.scope(owner)) != body["root"]
        ):
            raise ScheduleError("SCHEDULE_SOURCE_REVOKED")
        self.app.state.workflows.scope(owner)
        return kind, body

    def revoke(self, key, owner):
        with self.store.transaction() as db:
            row = db.get(SchedulerSourceRow, key)
            if not row or row.owner_id != owner:
                raise ScheduleError("SCHEDULE_SOURCE_NOT_FOUND", 404)
            row.body = encoded(json.loads(row.body) | {"enabled": False})

    def listing(self, owner):
        with self.store.factory() as db:
            return [
                {
                    "source_id": row.id,
                    "condition_type": row.kind,
                    **{
                        key: value
                        for key, value in json.loads(row.body).items()
                        if key in {"enabled", "expires_at", "workflow_run_id"}
                    },
                }
                for row in db.scalars(
                    select(SchedulerSourceRow)
                    .where(SchedulerSourceRow.owner_id == owner)
                    .limit(100)
                )
            ]

    def evaluate(self, definition, previous, now):
        config, owner = definition.trigger_config, definition.conversation_id
        kind, source = self.source(config.source_id, owner, now)
        if kind != config.condition_type:
            raise ScheduleError("SCHEDULE_SOURCE_INVALID")
        state = dict(previous)
        last_trigger = utc(state["last_triggered_at"]) if state.get("last_triggered_at") else None
        cooled = last_trigger is None or (now - last_trigger).total_seconds() >= config.cooldown
        if kind == "WORKFLOW_STATE":
            observed = self.app.state.workflows.repository.run(source["workflow_run_id"], owner)[
                "state"
            ]
            satisfied = observed == config.expected_value
            fire = state.get("last_observed_state") is False and satisfied and cooled
            # Initial state is a baseline, not a fabricated transition.
            state["last_observed_state"] = satisfied
            fingerprint = digest(
                {
                    "run": source["workflow_run_id"],
                    "state": observed,
                    "last": state.get("last_triggered_at"),
                }
            )
        else:
            observed = metadata_snapshot(source["root"])
            fire = False
            if "baseline" not in state:
                state["baseline"] = observed
            if state.get("candidate") != observed:
                state["candidate"], state["stable_since"] = observed, stamp(now)
            stable = (now - utc(state["stable_since"])).total_seconds() >= config.debounce
            if observed != state["baseline"] and stable and cooled:
                fire, state["baseline"] = True, observed
            fingerprint = digest(
                {
                    "source": config.source_id,
                    "state": observed,
                    "last": state.get("last_triggered_at"),
                }
            )
            state["last_observed_state"] = observed
        if fire:
            state["last_triggered_at"] = stamp(now)
        return state, fingerprint if fire else False

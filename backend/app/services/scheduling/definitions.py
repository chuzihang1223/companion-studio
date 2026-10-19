"""Definition management and explicit occurrence consent, separate from dispatch."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.recovery.checkpoints import digest
from app.services.scheduling.conditions import ConditionWatch
from app.services.scheduling.contracts import ScheduleDefinition, ScheduleError, TERMINAL
from app.services.scheduling.permissions import ScheduledPermissionAdapter
from app.services.scheduling.time_rules import first_fire, preview, stamp


class ScheduleDefinitions:
    def initialize_definitions(self):
        self.permissions = ScheduledPermissionAdapter(self.app)
        self.watch = ConditionWatch(self.app, self.store)

    def now(self):
        return self.clock() if self.clock else datetime.now(timezone.utc)

    def trace(self, kind, owner, schedule_id, occurrence_id=None, workflow_run_id=None):
        recorder = getattr(self.app.state, "trace_recorder", None)
        if recorder:
            context = recorder.create(str(uuid4()), owner)
            context.send(
                "event",
                event_type=kind,
                phase="task",
                metadata={
                    "schedule_id": schedule_id,
                    "occurrence_id": occurrence_id,
                    "workflow_run_id": workflow_run_id,
                },
            )
            context.finish()

    def validate(self, definition):
        snapshot = self.permissions.snapshot(definition)
        if definition.trigger_type == "CONDITION":
            kind, _ = self.watch.source(
                definition.trigger_config.source_id, definition.conversation_id, self.now()
            )
            if kind != definition.trigger_config.condition_type:
                raise ScheduleError("SCHEDULE_SOURCE_INVALID")
        return {
            "times": preview(definition, self.now()),
            "permissions": snapshot,
            "limits": {
                "max_active": 2,
                "max_queue": 32,
                "per_owner": 1,
                "min_interval_seconds": 300,
            },
        }

    def save(self, definition, revision=0):
        checked = self.validate(definition)
        result = self.store.save(
            definition,
            revision,
            checked["permissions"],
            first_fire(definition, self.now()),
            self.now(),
        )
        self.trace(
            "SCHEDULE_UPDATED" if revision else "SCHEDULE_CREATED",
            definition.conversation_id,
            definition.schedule_id,
        )
        return result

    def set_state(self, key, owner, revision, state):
        selected = self.store.get(key, owner)
        definition = ScheduleDefinition.model_validate(selected["definition"])
        due = None
        if state == "ENABLED":
            self.validate(definition)
            due = first_fire(definition, self.now())
        result = self.store.state(key, owner, revision, state, self.now(), due)
        self.trace("SCHEDULE_ENABLED" if state == "ENABLED" else "SCHEDULE_DISABLED", owner, key)
        return result

    def run_now(self, key, owner):
        selected = self.store.get(key, owner)
        if selected["state"] == "ARCHIVED":
            raise ScheduleError("SCHEDULE_ARCHIVED")
        if selected["generated"] >= selected["definition"]["max_consecutive_runs"]:
            raise ScheduleError("SCHEDULE_RUN_LIMIT")
        self.validate(ScheduleDefinition.model_validate(selected["definition"]))
        key = self.store.enqueue(selected, self.now(), None, {}, self.now(), manual=True)
        return self.store.occurrence(key, owner)

    def approval_overview(self, key, owner):
        row = self.store.occurrence(key, owner)
        definition = ScheduleDefinition.model_validate(row["body"]["definition"])
        snapshot = self.permissions.snapshot(definition)
        if snapshot != row["body"]["snapshot"]:
            raise ScheduleError("SCHEDULE_DEPENDENCY_CHANGED")
        original_revision = (
            self.app.state.workflows.repository.run(row["workflow_run_id"], owner)["revision"]
            if row["workflow_run_id"]
            else None
        )
        return {
            "name": definition.name,
            "occurrence_id": key,
            "scheduled_for": row["scheduled_for"],
            "workflow_version_id": definition.workflow_version_id,
            "may_use_models": snapshot["may_use_models"],
            "input_fields": len(definition.inputs),
            "nodes": snapshot["nodes"],
            "binding": digest(
                {
                    "owner": owner,
                    "occurrence": key,
                    "revision": row["revision"],
                    "snapshot": snapshot,
                    "status": row["status"],
                    "workflow_revision": original_revision,
                }
            ),
        }

    def grant_once(self, key, owner, binding=None):
        if binding is not None and self.approval_overview(key, owner)["binding"] != binding:
            raise ScheduleError("SCHEDULE_APPROVAL_EXPIRED")
        selected = self.store.occurrence(key, owner)
        if selected["status"] != "WAITING_PERMISSION" or selected["workflow_run_id"]:
            raise ScheduleError("SCHEDULE_APPROVAL_EXPIRED")
        definition = ScheduleDefinition.model_validate(selected["body"]["definition"])
        snapshot = self.permissions.snapshot(definition)
        self.validate(definition)
        if snapshot != selected["body"]["snapshot"]:
            raise ScheduleError("SCHEDULE_DEPENDENCY_CHANGED")
        permission = {
            "binding": digest(snapshot),
            "expires_at": stamp(self.now() + timedelta(minutes=5)),
        }
        self.store.transition(
            key,
            owner,
            "QUEUED",
            self.now(),
            updates={"permission": permission},
            expected={"WAITING_PERMISSION"},
        )
        return self.store.occurrence(key, owner)

    async def cancel(self, key, owner):
        selected = self.store.occurrence(key, owner)
        if selected["status"] in TERMINAL:
            raise ScheduleError("SCHEDULE_OCCURRENCE_TERMINAL")
        if selected["workflow_run_id"]:
            await self.app.state.workflows.control(selected["workflow_run_id"], owner, "cancel")
        self.store.transition(key, owner, "CANCELLED", self.now(), code="SCHEDULE_USER_CANCELLED")

    def authorize_node(self, run, node):
        key = run.get("schedule_occurrence_id")
        if not key:
            return
        selected = self.store.occurrence(key, run["conversation_id"])
        current = self.store.get(selected["schedule_id"], run["conversation_id"])
        if current["revision"] != selected["revision"] or (
            current["state"] != "ENABLED" and not selected["body"]["manual"]
        ):
            raise ScheduleError("SCHEDULE_EXECUTION_REVOKED")
        if (
            self.closing
            or not self.running
            or selected["status"] not in {"CLAIMED", "RUNNING", "WAITING_PERMISSION"}
        ):
            raise ScheduleError("SCHEDULE_EXECUTION_REVOKED")
        with self.store.factory() as db:
            self.store.leader(db, self.owner, self.token, self.now())
        definition = ScheduleDefinition.model_validate(selected["body"]["definition"])
        if definition.trigger_type == "CONDITION":
            self.watch.source(
                definition.trigger_config.source_id, definition.conversation_id, self.now()
            )
        inspected = self.permissions.inspect(
            definition,
            selected["body"]["snapshot"],
            selected["body"].get("permission"),
            self.now(),
            node,
        )
        if not inspected["allowed"]:
            raise ScheduleError("SCHEDULE_PERMISSION_REQUIRED")

    def remaining_timeout(self, run):
        selected = self.store.occurrence(run["schedule_occurrence_id"], run["conversation_id"])
        definition = ScheduleDefinition.model_validate(selected["body"]["definition"])
        # Workflow persists consumed monotonic execution time across attempts.
        # Downtime is neither a fresh budget nor execution time; wall-clock jumps
        # must not make an explicitly resumed safe task time out immediately.
        return max(0.001, definition.execution_timeout - run.get("active_ms", 0) / 1000)

    def status(self):
        queued = self.store.occurrences(statuses={"QUEUED", "PENDING", "DUE"}, limit=1000)
        waiting = self.store.occurrences(
            statuses={"WAITING_PERMISSION", "WAITING_ENVIRONMENT"}, limit=1000
        )
        return {
            "running": self.running and not self.closing,
            "paused": self.store.settings()["paused"],
            "queued": len(queued),
            "waiting_permission": len(waiting),
            "error_code": self.last_error,
            "background_requires_desktop_app": True,
            "startup_supported": False,
        }

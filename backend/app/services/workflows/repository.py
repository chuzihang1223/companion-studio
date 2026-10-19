"""Short transactions and optimistic revisions. No transaction spans an await/tool/user wait."""

from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.database.session import SessionLocal
from app.models.workflows import (
    WorkflowDefinitionRow,
    WorkflowVersionRow,
    WorkflowRunRow,
    WorkflowNodeRow,
    WorkflowRunEvent,
)
from app.services.recovery.checkpoints import digest, encoded
from app.services.workflows.contracts import Definition, WorkflowError, bounded


def now():
    return datetime.now(timezone.utc).isoformat()


def draft_dto(row):
    return {
        "definition": json.loads(row.body),
        "revision": row.revision,
        "updated_at": row.updated_at,
    }


class WorkflowRepository:
    def __init__(self, factory=SessionLocal):
        self.factory = factory

    def listing(self):
        with self.factory() as db:
            return [
                draft_dto(row)
                for row in db.scalars(
                    select(WorkflowDefinitionRow)
                    .order_by(WorkflowDefinitionRow.updated_at.desc())
                    .limit(200)
                )
            ]

    def get(self, workflow_id):
        with self.factory() as db:
            row = db.get(WorkflowDefinitionRow, workflow_id)
            if not row:
                raise WorkflowError("WORKFLOW_NOT_FOUND", status=404)
            return draft_dto(row)

    def save(self, definition: Definition, revision=0):
        body = definition.model_copy(update={"status": "DRAFT"}).model_dump_json()
        with self.factory() as db:
            if revision == 0:
                db.add(
                    WorkflowDefinitionRow(
                        id=definition.workflow_id, revision=1, body=body, updated_at=now()
                    )
                )
            else:
                result = db.execute(
                    update(WorkflowDefinitionRow)
                    .where(
                        WorkflowDefinitionRow.id == definition.workflow_id,
                        WorkflowDefinitionRow.revision == revision,
                    )
                    .values(body=body, revision=revision + 1, updated_at=now())
                )
                if result.rowcount != 1:
                    raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            try:
                db.commit()
            except IntegrityError:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409) from None
        return self.get(definition.workflow_id)

    def publish(self, definition, revision):
        with self.factory() as db:
            row = db.get(WorkflowDefinitionRow, definition.workflow_id)
            if (
                not row
                or row.revision != revision
                or json.loads(row.body)
                != definition.model_copy(update={"status": "DRAFT"}).model_dump(mode="json")
            ):
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            last = db.scalar(
                select(WorkflowVersionRow)
                .where(WorkflowVersionRow.workflow_id == row.id)
                .order_by(WorkflowVersionRow.number.desc())
            )
            number = last.number + 1 if last else 1
            published = definition.model_copy(update={"version": number, "status": "PUBLISHED"})
            data = published.model_dump(mode="json")
            key = str(uuid4())
            db.add(
                WorkflowVersionRow(
                    id=key,
                    workflow_id=row.id,
                    number=number,
                    body=encoded(data),
                    digest=digest(data),
                )
            )
            result = db.execute(
                update(WorkflowDefinitionRow)
                .where(
                    WorkflowDefinitionRow.id == row.id, WorkflowDefinitionRow.revision == revision
                )
                .values(
                    revision=revision + 1,
                    body=published.model_copy(update={"status": "DRAFT"}).model_dump_json(),
                    updated_at=now(),
                )
            )
            if result.rowcount != 1:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            try:
                db.commit()
            except IntegrityError:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409) from None
        return {
            "version_id": key,
            "definition": data,
            "digest": digest(data),
            "revision": revision + 1,
        }

    def versions(self, workflow_id):
        with self.factory() as db:
            return [
                {"version_id": r.id, "version": r.number, "digest": r.digest}
                for r in db.scalars(
                    select(WorkflowVersionRow)
                    .where(WorkflowVersionRow.workflow_id == workflow_id)
                    .order_by(WorkflowVersionRow.number.desc())
                    .limit(100)
                )
            ]

    def version(self, version_id):
        with self.factory() as db:
            row = db.get(WorkflowVersionRow, version_id)
            if not row or digest(json.loads(row.body)) != row.digest:
                raise WorkflowError("WORKFLOW_VERSION_NOT_FOUND", status=404)
            return Definition.model_validate_json(row.body)

    def delete(self, workflow_id, revision):
        # Only authoring is deleted. Immutable versions and historical runs remain.
        with self.factory() as db:
            result = db.execute(
                delete(WorkflowDefinitionRow).where(
                    WorkflowDefinitionRow.id == workflow_id,
                    WorkflowDefinitionRow.revision == revision,
                )
            )
            if result.rowcount != 1:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            db.commit()

    def archive(self, workflow_id, revision):
        with self.factory() as db:
            row = db.get(WorkflowDefinitionRow, workflow_id)
            if not row or row.revision != revision:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            body = json.loads(row.body) | {"status": "ARCHIVED"}
            result = db.execute(
                update(WorkflowDefinitionRow)
                .where(
                    WorkflowDefinitionRow.id == workflow_id,
                    WorkflowDefinitionRow.revision == revision,
                )
                .values(body=encoded(body), revision=revision + 1, updated_at=now())
            )
            if result.rowcount != 1:
                raise WorkflowError("WORKFLOW_EDIT_CONFLICT", status=409)
            db.commit()
        return self.get(workflow_id)

    def create_run(
        self,
        definition,
        version_id,
        conversation_id,
        inputs,
        renderer_id,
        *,
        run_id=None,
        schedule_occurrence_id=None,
        schedule_lease_token=None,
    ):
        key = run_id or str(uuid4())
        body = {
            "run_id": key,
            "workflow_id": definition.workflow_id,
            "version_id": version_id,
            "version": definition.version,
            "task_id": str(uuid4()),
            "conversation_id": conversation_id,
            "renderer_id": renderer_id,
            "inputs": bounded(inputs),
            "output": None,
            "state": "PENDING",
            "model_calls": 0,
            "tool_calls": 0,
            "attempt": 1,
            "active_ms": 0,
            "error_code": None,
            "trace_id": None,
            "work_session_id": None,
        }
        if schedule_occurrence_id:
            body["schedule_occurrence_id"] = schedule_occurrence_id
        with self.factory() as db:
            if schedule_occurrence_id:
                from sqlalchemy import text
                from app.models.schedules import ScheduledOccurrenceRow, ScheduledRunRow

                db.execute(text("BEGIN IMMEDIATE"))
                occurrence = db.get(ScheduledOccurrenceRow, schedule_occurrence_id)
                scheduled = db.get(ScheduledRunRow, occurrence.run_id) if occurrence else None
                if (
                    not occurrence
                    or not scheduled
                    or occurrence.owner_id != conversation_id
                    or occurrence.status != "CLAIMED"
                    or occurrence.workflow_run_id
                    or occurrence.lease_token != schedule_lease_token
                    or json.loads(scheduled.body)["workflow_run_id"] != key
                    or json.loads(occurrence.body)["definition"]["workflow_version_id"]
                    != version_id
                ):
                    raise WorkflowError("SCHEDULE_DISPATCH_CONFLICT", status=409)
                # The occurrence binding and original WorkflowRun become visible
                # in one transaction, before launch can execute a single node.
                occurrence.workflow_run_id = key
                scheduled.body = encoded(json.loads(scheduled.body) | {"task_id": body["task_id"]})
            db.add(
                WorkflowRunRow(
                    id=key,
                    workflow_id=definition.workflow_id,
                    version_id=version_id,
                    conversation_id=conversation_id,
                    state="PENDING",
                    body=encoded(body),
                    created_at=now(),
                )
            )
            for node in definition.nodes:
                node_id = str(uuid4())
                db.add(
                    WorkflowNodeRow(
                        id=node_id,
                        run_id=key,
                        node_id=node.node_id,
                        body=encoded(
                            {
                                "node_id": node.node_id,
                                "node_execution_id": node_id,
                                "state": "PENDING",
                                "output": None,
                                "attempt": 0,
                                "error_code": None,
                                "duration_ms": 0,
                            }
                        ),
                    )
                )
            db.commit()
        return key

    def run(self, run_id, conversation_id):
        with self.factory() as db:
            row = db.get(WorkflowRunRow, run_id)
            if not row or row.conversation_id != conversation_id:
                raise WorkflowError("WORKFLOW_RUN_NOT_FOUND", status=404)
            nodes = [
                json.loads(n.body)
                for n in db.scalars(select(WorkflowNodeRow).where(WorkflowNodeRow.run_id == run_id))
            ]
            return json.loads(row.body) | {
                "revision": row.revision,
                "created_at": row.created_at,
                "nodes": nodes,
            }

    def runs(self, conversation_id, workflow_id=None):
        with self.factory() as db:
            query = select(WorkflowRunRow).where(WorkflowRunRow.conversation_id == conversation_id)
            if workflow_id:
                query = query.where(WorkflowRunRow.workflow_id == workflow_id)
            return [
                json.loads(row.body) | {"revision": row.revision, "created_at": row.created_at}
                for row in db.scalars(query.order_by(WorkflowRunRow.created_at.desc()).limit(100))
            ]

    def change(
        self,
        run_id,
        conversation_id,
        changes,
        *,
        node_id=None,
        node_changes=None,
        expected_revision=None,
        event=None,
    ):
        with self.factory() as db:
            row = db.get(WorkflowRunRow, run_id)
            if not row or row.conversation_id != conversation_id:
                raise WorkflowError("WORKFLOW_RUN_NOT_FOUND", status=404)
            if expected_revision is not None and row.revision != expected_revision:
                raise WorkflowError("WORKFLOW_RUN_CONFLICT", status=409)
            body = json.loads(row.body) | changes
            result = db.execute(
                update(WorkflowRunRow)
                .where(WorkflowRunRow.id == run_id, WorkflowRunRow.revision == row.revision)
                .values(body=encoded(body), state=body["state"], revision=row.revision + 1)
            )
            if result.rowcount != 1:
                raise WorkflowError("WORKFLOW_RUN_CONFLICT", status=409)
            if node_id:
                node = db.scalar(
                    select(WorkflowNodeRow).where(
                        WorkflowNodeRow.run_id == run_id, WorkflowNodeRow.node_id == node_id
                    )
                )
                if not node:
                    raise WorkflowError("WORKFLOW_NODE_NOT_FOUND", node_id, status=404)
                node.body = encoded(json.loads(node.body) | (node_changes or {}))
            if event:
                db.add(
                    WorkflowRunEvent(
                        run_id=run_id,
                        body=encoded(
                            {
                                "kind": event,
                                "node_id": node_id,
                                "state": (node_changes or changes).get("state"),
                                "created_at": now(),
                            }
                        ),
                    )
                )
            db.commit()

    def events(self, run_id, conversation_id, after=0):
        self.run(run_id, conversation_id)
        with self.factory() as db:
            return [
                {"cursor": e.id, **json.loads(e.body)}
                for e in db.scalars(
                    select(WorkflowRunEvent)
                    .where(WorkflowRunEvent.run_id == run_id, WorkflowRunEvent.id > after)
                    .order_by(WorkflowRunEvent.id)
                    .limit(200)
                )
            ]

    def recover(self):
        with self.factory() as db:
            rows = list(
                db.scalars(
                    select(WorkflowRunRow).where(
                        WorkflowRunRow.state.in_(
                            {"PENDING", "READY", "RUNNING", "WAITING_PERMISSION", "PAUSED"}
                        )
                    )
                )
            )
        for row in rows:
            self.change(
                row.id,
                row.conversation_id,
                {"state": "INTERRUPTED", "error_code": "PROCESS_RESTARTED"},
                event="WORKFLOW_INTERRUPTED",
            )
            data = self.run(row.id, row.conversation_id)
            for node in data["nodes"]:
                if node["state"] in {"READY", "RUNNING", "WAITING_PERMISSION"}:
                    self.change(
                        row.id,
                        row.conversation_id,
                        {},
                        node_id=node["node_id"],
                        node_changes={"state": "INTERRUPTED"},
                    )

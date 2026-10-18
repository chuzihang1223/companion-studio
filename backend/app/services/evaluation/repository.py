"""有界评测索引。评分通过原子条件更新最多提交一次，历史读取不执行任务。"""

from datetime import datetime, timezone
import json
from uuid import uuid4

from sqlalchemy import delete, select, update

from app.database.session import SessionLocal
from app.models.evaluation import EvaluationAttempt, EvaluationRun
from app.services.evaluation.metrics import MetricsCollector, compare_conditions

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "INTERRUPTED"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def attempt_dto(row):
    return {
        "attempt_id": row.id,
        "run_id": row.run_id,
        "case_id": row.case_id,
        "repetition_index": row.repetition_index,
        "execution_status": row.execution_status,
        "grader_status": row.grader_status,
        "task_id": row.task_id,
        "trace_id": row.trace_id,
        "conversation_id": row.conversation_id,
        "work_session_id": row.work_session_id,
        "duration_ms": row.duration_ms,
        "error_code": row.error_code,
        "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "result": json.loads(row.result_json),
    }


def run_dto(row):
    metrics = json.loads(row.metrics_json)
    counts = metrics.get("counts", {})
    return {
        "run_id": row.id,
        "status": row.status,
        "mode": row.mode,
        "snapshot": json.loads(row.snapshot_json),
        "options": json.loads(row.request_json),
        "metrics": metrics,
        "total_cases": row.total,
        "completed_cases": metrics.get("total_count", 0),
        "passed_cases": counts.get("PASS", 0),
        "failed_cases": counts.get("FAIL", 0),
        "blocked_cases": counts.get("BLOCKED", 0),
        "revision": row.revision,
        "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "started_at": row.started_at.replace(tzinfo=timezone.utc).isoformat()
        if row.started_at
        else None,
        "completed_at": row.completed_at.replace(tzinfo=timezone.utc).isoformat()
        if row.completed_at
        else None,
    }


class EvaluationRepository:
    def __init__(self, factory=SessionLocal):
        self.factory = factory

    def create(self, options, cases, snapshot):
        run_id = str(uuid4())
        with self.factory() as db:
            # Active and historical limits are checked before creating workspaces.
            rows = list(db.scalars(select(EvaluationRun)))
            if len(rows) >= 100 or any(row.status not in TERMINAL for row in rows):
                raise ValueError("请等待当前评测结束；最多保留 100 次运行，可删除旧历史")
            db.add(
                EvaluationRun(
                    id=run_id,
                    mode=options.mode.value,
                    total=len(cases) * options.repetitions,
                    request_json=dumps(options.model_dump(mode="json", exclude={"visual_consent"})),
                    snapshot_json=dumps(snapshot),
                )
            )
            for case in cases:
                for index in range(options.repetitions):
                    db.add(
                        EvaluationAttempt(
                            id=str(uuid4()),
                            run_id=run_id,
                            case_id=case.case_id,
                            repetition_index=index,
                        )
                    )
            db.commit()
        return self.detail(run_id)

    @staticmethod
    def owned(db, run_id):
        row = db.get(EvaluationRun, run_id)
        if not row:
            raise LookupError("评测不存在")
        return row

    def detail(self, run_id):
        with self.factory() as db:
            return run_dto(self.owned(db, run_id))

    def listing(self, cursor=0, limit=20):
        with self.factory() as db:
            rows = list(
                db.scalars(
                    select(EvaluationRun)
                    .order_by(EvaluationRun.created_at.desc())
                    .offset(cursor)
                    .limit(limit + 1)
                )
            )
            return {
                "items": [run_dto(row) for row in rows[:limit]],
                "next_cursor": cursor + limit if len(rows) > limit else None,
            }

    def results(self, run_id, cursor=0, limit=20):
        with self.factory() as db:
            self.owned(db, run_id)
            rows = list(
                db.scalars(
                    select(EvaluationAttempt)
                    .where(EvaluationAttempt.run_id == run_id)
                    .order_by(EvaluationAttempt.case_id, EvaluationAttempt.repetition_index)
                    .offset(cursor)
                    .limit(limit + 1)
                )
            )
            return {
                "items": [attempt_dto(row) for row in rows[:limit]],
                "next_cursor": cursor + limit if len(rows) > limit else None,
            }

    def attempt(self, run_id, attempt_id):
        with self.factory() as db:
            self.owned(db, run_id)
            row = db.get(EvaluationAttempt, attempt_id)
            if not row or row.run_id != run_id:
                raise LookupError("评测尝试不存在")
            return attempt_dto(row)

    def status(self, run_id, status):
        with self.factory() as db:
            row = self.owned(db, run_id)
            if row.status in TERMINAL:
                return
            row.status, row.revision = status, row.revision + 1
            if status == "RUNNING" and row.started_at is None:
                row.started_at = datetime.now(timezone.utc)
            if status in TERMINAL:
                row.completed_at = datetime.now(timezone.utc)
            db.commit()

    def started(self, attempt_id):
        with self.factory() as db:
            db.execute(
                update(EvaluationAttempt)
                .where(
                    EvaluationAttempt.id == attempt_id, EvaluationAttempt.grader_status == "PENDING"
                )
                .values(execution_status="RUNNING")
            )
            db.commit()

    def finish(self, run_id, attempt_id, result, links=None):
        links = links or {}
        encoded = dumps(result)
        if len(encoded) > 24000:
            raise ValueError("评测证据超过大小限制")
        with self.factory() as db:
            self.owned(db, run_id)
            updated = db.execute(
                update(EvaluationAttempt)
                .where(
                    EvaluationAttempt.id == attempt_id,
                    EvaluationAttempt.run_id == run_id,
                    EvaluationAttempt.grader_status == "PENDING",
                )
                .values(
                    execution_status=result["completion_status"],
                    grader_status="GRADED",
                    result_json=encoded,
                    task_id=links.get("task_id"),
                    trace_id=links.get("trace_id"),
                    conversation_id=links.get("conversation_id"),
                    work_session_id=links.get("work_session_id"),
                    duration_ms=result.get("duration_ms"),
                    error_code=result.get("error_code"),
                )
            )
            db.commit()
            return updated.rowcount == 1

    def refresh_metrics(self, run_id):
        with self.factory() as db:
            row = self.owned(db, run_id)
            attempts = list(
                db.scalars(select(EvaluationAttempt).where(EvaluationAttempt.run_id == run_id))
            )
            results = [
                json.loads(item.result_json) for item in attempts if item.grader_status == "GRADED"
            ]
            metrics = MetricsCollector.aggregate(results, row.mode)
            row.metrics_json, row.revision = dumps(metrics), row.revision + 1
            db.commit()
            return metrics

    def recover(self):
        with self.factory() as db:
            ids = list(
                db.scalars(select(EvaluationRun.id).where(EvaluationRun.status.not_in(TERMINAL)))
            )
        for run_id in ids:
            for item in self.results(run_id, limit=100)["items"]:
                if item["grader_status"] == "PENDING":
                    self.finish(
                        run_id,
                        item["attempt_id"],
                        empty_result(
                            "UNKNOWN" if item["execution_status"] == "RUNNING" else "NOT_RUN",
                            "INTERRUPTED",
                        ),
                    )
            self.refresh_metrics(run_id)
            self.status(run_id, "INTERRUPTED")

    def attach_recovered_trace(self, run_id, attempt_id, links):
        with self.factory() as db:
            row = db.get(EvaluationAttempt, attempt_id)
            if not row or row.run_id != run_id or row.trace_id:
                return
            for key in ("trace_id", "task_id", "conversation_id", "work_session_id"):
                setattr(row, key, links.get(key))
            db.commit()

    def remove(self, run_id):
        with self.factory() as db:
            row = self.owned(db, run_id)
            if row.status not in TERMINAL:
                raise ValueError("请先结束评测")
            db.execute(delete(EvaluationAttempt).where(EvaluationAttempt.run_id == run_id))
            db.delete(row)
            db.commit()
        return {"deleted": True}

    def comparison(self, left, right):
        a, b = self.detail(left), self.detail(right)
        return {
            "left": a,
            "right": b,
            **compare_conditions(a["snapshot"], b["snapshot"]),
            "cases": {
                "left": self.results(left, limit=100)["items"],
                "right": self.results(right, limit=100)["items"],
            },
        }


def empty_result(status, code):
    return {
        "completion_status": status,
        "passed": None,
        "score": None,
        "grader_type": "not_run",
        "error_code": code,
        "duration_ms": None,
        "tool_calls": 0,
        "model_requests": 0,
        "retry_count": 0,
        "model_usage": {"status": "Unavailable"},
        "evidence_refs": [],
        "evidence": {},
        "explanation": "未获得有效评分证据；不计入通过率分母。",
    }

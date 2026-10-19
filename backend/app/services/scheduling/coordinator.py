"""Durable trigger/admission loop. Only the original WorkflowCoordinator executes nodes."""

import asyncio
from datetime import timedelta
import logging
import time
from uuid import uuid4

from app.services.scheduling.contracts import ScheduleDefinition, ScheduleError
from app.services.scheduling.definitions import ScheduleDefinitions
from app.services.scheduling.monitor import ScheduledRunMonitor
from app.services.scheduling.recovery import SchedulerRecovery
from app.services.scheduling.process_owner import process_identity, owner_alive
from app.services.scheduling.store import ScheduleStore
from app.services.scheduling.time_rules import next_fire, utc


class DurableScheduler(ScheduleDefinitions, ScheduledRunMonitor, SchedulerRecovery):
    def __init__(self, app, store=None, clock=None, alive=None):
        self.app, self.store, self.clock = app, store or ScheduleStore(), clock
        self.owner, self.token = str(uuid4()), str(uuid4())
        self.identity = process_identity()
        self.alive = alive or owner_alive
        self.running = self.closing = self.recovered = False
        self.last_error = None
        self.task = None
        # An owned in-memory fallback is essential during SQLite failure at Quit.
        # Durable rows remain the scheduling truth; this map is only for cleanup.
        self.owned_runs = {}
        self.initialize_definitions()

    def start(self):
        if self.task is not None:
            raise ScheduleError("SCHEDULER_ALREADY_STARTED")
        self.task = asyncio.create_task(self.loop())

    async def loop(self):
        while not self.closing:
            started = time.monotonic()
            try:
                await self.tick()
                if self.running:
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = getattr(error, "code", "SCHEDULER_STORAGE_OR_RUNTIME_UNAVAILABLE")
                logging.getLogger(__name__).warning("scheduler_tick_failed")
            await asyncio.sleep(max(0.05, 1 - (time.monotonic() - started)))

    async def tick(self):
        if self.closing:
            return
        now = self.now()
        self.running = await asyncio.to_thread(
            self.store.lease, self.owner, self.identity, self.token, now, self.alive
        )
        if not self.running:
            self.last_error = "SCHEDULER_OTHER_INSTANCE_ACTIVE"
            return
        if not self.recovered:
            await asyncio.to_thread(self.recover)
            self.recovered = True
        await asyncio.to_thread(self.store.renew_runs, self.owner, self.token, now)
        await asyncio.to_thread(self.reconcile)
        if self.store.settings()["paused"]:
            return
        for selected in await asyncio.to_thread(self.store.listing, due=now):
            await self.trigger(selected, now)
        # Queue order is oldest first; a small fixed upper bound prevents an
        # enormous backlog from monopolizing the event loop after resume.
        for row in reversed(
            self.store.occurrences(statuses={"QUEUED", "DUE", "PENDING"}, limit=32)
        ):
            await self.dispatch(row)

    async def trigger(self, selected, now):
        definition = ScheduleDefinition.model_validate(selected["definition"])
        due = utc(selected["next_due"])
        following = next_fire(definition, now, utc(selected["created_at"]))
        state, fingerprint = {}, None
        status, code = "QUEUED", None
        try:
            if definition.trigger_type == "CONDITION":
                state, fingerprint = await asyncio.to_thread(
                    self.watch.evaluate, definition, selected["condition_state"], now
                )
            elif (now - due).total_seconds() > 5:
                if (
                    definition.misfire_policy == "SKIP_MISSED"
                    or (now - due).total_seconds() > definition.expiry_window
                ):
                    status, code = "MISSED", "SCHEDULE_MISSED"
                elif definition.misfire_policy == "REQUIRE_REVIEW":
                    status, code = "NEEDS_REVIEW", "SCHEDULE_MISFIRE_REVIEW"
                # RUN_ONCE_AFTER_RESUME still passes current permission preflight.
            key = await asyncio.to_thread(
                self.store.enqueue,
                selected,
                due,
                following,
                state,
                now,
                condition_fingerprint=fingerprint,
                status=status,
                code=code,
            )
        except Exception as error:
            key = await asyncio.to_thread(
                self.store.enqueue,
                selected,
                due,
                None,
                state,
                now,
                status="NEEDS_REVIEW",
                code=getattr(error, "code", "SCHEDULE_SOURCE_UNAVAILABLE"),
            )
        if key:
            self.trace(
                "OCCURRENCE_CREATED", definition.conversation_id, definition.schedule_id, key
            )

    async def dispatch(self, row):
        owner, key = row["owner_id"], row["occurrence_id"]
        definition = ScheduleDefinition.model_validate(row["body"]["definition"])
        try:
            if definition.trigger_type == "CONDITION":
                await asyncio.to_thread(
                    self.watch.source, definition.trigger_config.source_id, owner, self.now()
                )
            inspected = await asyncio.to_thread(
                self.permissions.inspect,
                definition,
                row["body"]["snapshot"],
                row["body"].get("permission"),
                self.now(),
            )
            if not inspected["allowed"]:
                self.store.transition(
                    key,
                    owner,
                    "WAITING_PERMISSION",
                    self.now(),
                    code="SCHEDULE_PERMISSION_REQUIRED",
                    expected={"QUEUED", "DUE", "PENDING"},
                )
                return
            claimed = await asyncio.to_thread(
                self.store.claim, key, self.owner, self.token, self.now()
            )
            if not claimed:
                return
            self.owned_runs[claimed["reserved_workflow_id"]] = owner
            # create_run atomically binds the reserved occurrence and workflow
            # rows before launch() can run any node. A crash never creates a new ID.
            run = self.app.state.workflows.start(
                definition.workflow_id,
                definition.workflow_version_id,
                owner,
                definition.inputs,
                "scheduler:" + key,
                run_id=claimed["reserved_workflow_id"],
                schedule_occurrence_id=key,
                schedule_lease_token=claimed["lease_token"],
            )
            self.store.settle(
                key,
                owner,
                "RUNNING",
                {"task_id": run["task_id"], "workflow_run_id": run["run_id"]},
                self.now(),
            )
            self.trace("RUN_STARTED", owner, row["schedule_id"], key, run["run_id"])
        except Exception as error:
            # If start committed but launch failed, retain the existing row and
            # require reconciliation; never call start a second time here.
            current = self.store.occurrence(key, owner)
            self.store.transition(
                key,
                owner,
                "NEEDS_REVIEW" if current["run_id"] else "WAITING_PERMISSION",
                self.now(),
                code=getattr(error, "code", "SCHEDULE_PREFLIGHT_FAILED"),
            )

    async def close(self):
        if self.closing:
            return
        self.closing = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        # Only runs claimed by this instance are cancelled. Ordinary Work/Chat
        # and another product's processes are outside scheduler ownership.
        rows = []
        try:
            rows = [
                r
                for r in self.store.occurrences(
                    statuses={"CLAIMED", "RUNNING", "WAITING_PERMISSION"}, limit=1000
                )
                if r["lease_owner"] == self.owner
            ]
            self.owned_runs.update(
                {r["workflow_run_id"]: r["owner_id"] for r in rows if r["workflow_run_id"]}
            )
        except Exception:
            self.last_error = "SCHEDULER_SHUTDOWN_STORAGE_UNAVAILABLE"
            logging.getLogger(__name__).warning("scheduler_shutdown_storage_unavailable")
        try:
            for run_id, owner in list(self.owned_runs.items()):
                task = self.app.state.workflows.tasks.get(run_id)
                if not task:
                    continue
                try:
                    await self.app.state.workflows.interrupt(run_id, owner)
                except Exception:
                    # Original interrupt can fail before cancelling when its DB
                    # is busy. Cancel only this recorded coroutine, never all runs.
                    self.app.state.workflows.interrupting.add(run_id)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            for row in rows:
                try:
                    self.store.transition(
                        row["occurrence_id"],
                        row["owner_id"],
                        "INTERRUPTED",
                        self.now(),
                        code="SCHEDULE_APPLICATION_QUIT",
                    )
                except Exception:
                    self.last_error = "SCHEDULER_SHUTDOWN_STORAGE_UNAVAILABLE"
        finally:
            try:
                await asyncio.to_thread(self.store.release, self.owner, self.token)
            except Exception:
                self.last_error = "SCHEDULER_SHUTDOWN_STORAGE_UNAVAILABLE"
            self.running = False
            self.owned_runs.clear()

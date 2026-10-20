"""Narrow adapters to original coordinators, never nested executors/approvals."""

from uuid import UUID


def requested(text):
    return any(
        word in text.casefold()
        for word in ("工作流", "定时任务", "定时计划", "workflow", "scheduler")
    )


def validate_discovered_plan(calls):
    if any(not call["name"].startswith(("workflow.", "scheduler.")) for call in calls):
        raise ValueError("本次工作流/定时任务发现仅允许编排工具；其他操作请单独发起。")


def planning_context(app, owner, text):
    """Bounded discovery metadata for name-based requests, never graph/inputs.

    Names are untrusted data. Only immutable IDs/revisions assist planning; this
    does not grant execution permission or expose another conversation's jobs.
    """
    if not owner or not requested(text):
        return {}
    result = {}
    workflows = getattr(app.state, "workflows", None)
    if workflows:
        result["workflows"] = [
            {
                "workflow_id": row["definition"]["workflow_id"],
                "name": row["definition"]["name"],
                "versions": workflows.repository.versions(row["definition"]["workflow_id"])[:3],
            }
            for row in workflows.repository.listing()[:20]
            if row["definition"]["status"] != "ARCHIVED"
        ]
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler:
        result["schedules"] = [
            {
                "schedule_id": row["definition"]["schedule_id"],
                "name": row["definition"]["name"],
                "revision": row["revision"],
                "state": row["state"],
            }
            for row in scheduler.store.listing(str(UUID(owner)))[:20]
        ]
    return result


def definitions():
    identity = {"type": "string", "minLength": 36, "maxLength": 36}
    rows = [
        (
            "workflow.list",
            "List locally published workflow IDs and immutable version IDs; no execution permission.",
            {},
            [],
            "low",
        ),
        (
            "workflow.run",
            "Queue one exact published workflow version for the current Work conversation after native tool approval. Queued is not completed. Its nodes retain all original permissions.",
            {
                "workflow_id": identity,
                "version_id": identity,
                "inputs": {"type": "object", "maxProperties": 30},
            },
            ["workflow_id", "version_id", "inputs"],
            "high",
        ),
        (
            "scheduler.list",
            "List scheduled definitions in this Work conversation, without their private inputs.",
            {},
            [],
            "low",
        ),
        (
            "scheduler.status",
            "Read status counts only for this conversation's scheduled definitions.",
            {},
            [],
            "low",
        ),
        (
            "scheduler.pause",
            "Disable one scheduled definition at an exact revision after native approval. Does not cancel an already running occurrence.",
            {"schedule_id": identity, "revision": {"type": "integer", "minimum": 1}},
            ["schedule_id", "revision"],
            "high",
        ),
    ]
    return [
        dict(
            name=name,
            description=description,
            properties=properties,
            required=required,
            risk_level=risk,
            requires_confirmation=risk == "high",
        )
        for name, description, properties, required, risk in rows
    ]


async def execute(app, context, call):
    # A workflow node cannot queue another workflow/schedule control, avoiding
    # recursion and a child run waiting for the same original Agent lock.
    if getattr(context, "workflow_run_id", None):
        return {"verified": False, "code": "ORCHESTRATION_NESTING_DENIED"}
    name, args = call["name"], call["arguments"]
    owner = str(UUID(context.conversation_id))
    if name == "workflow.list":
        repository = app.state.workflows.repository
        items = []
        for row in repository.listing():
            definition = row["definition"]
            if definition["status"] != "ARCHIVED":
                versions = repository.versions(definition["workflow_id"])
                if versions:
                    items.append(
                        {
                            "workflow_id": definition["workflow_id"],
                            "name": definition["name"],
                            "versions": versions[:5],
                        }
                    )
        return {"verified": True, "items": items[:50]}
    if name == "workflow.run":
        run = app.state.workflows.start(
            str(UUID(args["workflow_id"])),
            str(UUID(args["version_id"])),
            owner,
            args["inputs"],
            context.renderer_id,
        )
        # Never await completion here: inner nodes acquire this conversation's
        # Agent lock after this tool returns and releases the outer task.
        return {
            "verified": True,
            "run_id": run["run_id"],
            "status": "queued",
            "completed": False,
            "node_permissions_unchanged": True,
        }
    scheduler = app.state.scheduler
    if name == "scheduler.pause":
        result = scheduler.set_state(
            str(UUID(args["schedule_id"])), owner, args["revision"], "DISABLED"
        )
        return {
            "verified": result["state"] == "DISABLED",
            "schedule_id": result["definition"]["schedule_id"],
            "revision": result["revision"],
            "state": result["state"],
            "running_occurrences_cancelled": False,
        }
    rows = scheduler.store.listing(owner)
    if name == "scheduler.status":
        return {
            "verified": True,
            "total": len(rows),
            "enabled": sum(r["state"] == "ENABLED" for r in rows),
            "disabled": sum(r["state"] == "DISABLED" for r in rows),
        }
    if name == "scheduler.list":
        return {
            "verified": True,
            "items": [
                {
                    "schedule_id": row["definition"]["schedule_id"],
                    "revision": row["revision"],
                    "state": row["state"],
                }
                for row in rows[:50]
            ],
        }
    raise ValueError("ORCHESTRATION_TOOL_INVALID")

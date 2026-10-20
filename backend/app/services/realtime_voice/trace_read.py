"""Read original Trace DTOs, including chat-owned voice traces; never replay audio."""

import asyncio
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .contracts import Strict


class TraceRead(Strict):
    operation: Literal["list", "detail", "events", "summary"]
    conversation_id: UUID
    trace_id: UUID | None = None
    task_id: UUID | None = None
    status: (
        Literal[
            "planning",
            "ready",
            "queued",
            "running",
            "paused",
            "waiting_permission",
            "completed",
            "failed",
            "cancelled",
            "interrupted",
        ]
        | None
    ) = None
    cursor: int = Field(default=0, ge=0, le=100000)
    after: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=50, ge=1, le=250)
    since: datetime | None = None
    until: datetime | None = None
    tool_name: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.]{0,79}$")


async def read(repository, options):
    conversation = str(options.conversation_id)
    if options.operation == "list":
        return await asyncio.to_thread(
            repository.listing,
            conversation,
            voice=True,
            task_id=str(options.task_id) if options.task_id else None,
            status=options.status,
            cursor=options.cursor,
            limit=min(100, options.limit),
            since=options.since,
            until=options.until,
            tool_name=options.tool_name,
        )
    if not options.trace_id:
        raise ValueError("VOICE_TRACE_ID_REQUIRED")
    arguments = {"voice": True}
    if options.operation == "events":
        arguments.update(after=options.after, limit=options.limit)
    return await asyncio.to_thread(
        getattr(repository, options.operation), str(options.trace_id), conversation, **arguments
    )

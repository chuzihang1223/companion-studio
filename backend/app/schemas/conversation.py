from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreate(BaseModel):
    character_id: str = Field(min_length=1, max_length=100)
    conversation_type: Literal['chat', 'work', 'project'] = 'chat'
    project_id: str | None = Field(default=None, max_length=36)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class ConversationMode(BaseModel):
    preferred_mode: Literal["chat", "work"]


class BranchCreate(BaseModel):
    fork_message_id: str | None = None


class ConversationInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    character_id: str
    title: str
    preferred_mode: str
    conversation_type: str
    project_id: str | None = None
    work_session_id: str | None = None
    parent_conversation_id: str | None
    fork_message_id: str | None
    created_at: datetime
    updated_at: datetime


class ConversationMessage(BaseModel):
    id: str
    character_id: str
    conversation_id: str | None
    role: str
    content: str
    created_at: datetime
    reply_to_message_id: str | None
    variant_of_message_id: str | None
    voice_status: str = "none"
    voice_engine: str | None = None
    attachments: list[dict] = Field(default_factory=list)

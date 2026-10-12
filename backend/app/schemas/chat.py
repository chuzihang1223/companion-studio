from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AttachmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    data_base64: str = Field(min_length=1, max_length=15_000_000)


class ChatRequest(BaseModel):
    renderer_id: str | None = Field(default=None,max_length=100)
    character_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    reply_to_message_id: str | None = None
    attachments: list[AttachmentInput] = Field(default_factory=list, max_length=4)


class ChatStreamRequest(ChatRequest):
    expression: Literal["restrained", "natural", "rich"] = "natural"
    voice_enabled: bool = True
    voice_prefetch: bool = True
    speed_factor: float = Field(default=1.0, ge=0.9, le=1.1)


class ChatResponse(BaseModel):
    message_id: str
    character_id: str
    content: str
    created_at: datetime
    conversation_id: str | None = None


class HistoryMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    character_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime

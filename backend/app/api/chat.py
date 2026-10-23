import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Conversation, Message
from app.schemas.chat import ChatRequest, ChatResponse, HistoryMessage
from app.services.character.prompt_builder import CharacterPromptBuilder
from app.services.character.relationship_memory import relationship_memory
from app.services.character.personalization import read_settings, relevant_manual
from app.services.character.loader import DEFAULT_CHARACTER_ID
from app.services.knowledge import chat_integration as knowledge_chat
from app.services.conversation import latest_or_create, touch_after_user_message, visible_messages
from app.services.llm.openai_provider import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMResponseError,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)


@router.post("/{message_id}/retry", response_model=ChatResponse)
async def retry_answer(
    message_id: str, request: Request, db: Session = Depends(get_db)
) -> ChatResponse:
    old = db.get(Message, message_id)
    if old is None or old.role != "assistant" or not old.conversation_id:
        raise HTTPException(status_code=404, detail="Reply not found")
    conversation = db.get(Conversation, old.conversation_id)
    if conversation is None or conversation.is_deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    visible = visible_messages(db, conversation)
    position = next((index for index, row in enumerate(visible) if row.id == message_id), None)
    if position is None or position < 1 or visible[position - 1].role != "user":
        raise HTTPException(status_code=409, detail="Original user message not found")
    original = visible[position - 1]
    if original.attachments_json and original.attachments_json != "[]":
        raise HTTPException(status_code=409, detail="含附件的消息请创建分支后重新发送附件")
    character = request.app.state.characters.get(old.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")
    selected_service = request.app.state.model_roles.service("character_model")
    history = knowledge_chat.history(
        request.app,
        visible[max(0, position - 21) : position - 1],
        conversation.id,
        old.character_id,
        selected_service,
    )
    memory = relationship_memory(
        db, old.character_id, {row.id for row in visible[max(0, position - 21) : position]}
    )
    prompt = CharacterPromptBuilder().build(
        character,
        history,
        original.content,
        memory_context=memory,
        personalization=read_settings(db, old.character_id),
        manual_memory=knowledge_chat.manual(
            request.app,
            relevant_manual(db, old.character_id, original.content),
            conversation.id,
            old.character_id,
            original.content,
        ),
    )
    alternative_id = str(uuid4())
    service = await knowledge_chat.grounded(
        request.app,
        selected_service,
        conversation.id,
        old.character_id,
        original.content,
        alternative_id,
    )
    try:
        reply = await service.chat(prompt)
    except (LLMConfigurationError, LLMAuthenticationError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not regenerate reply")
        raise HTTPException(status_code=502, detail="Model request failed") from exc
    try:
        alternative = Message(
            id=alternative_id,
            character_id=old.character_id,
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            variant_of_message_id=old.id,
        )
        db.add(alternative)
        db.commit()
        db.refresh(alternative)
        return ChatResponse(
            message_id=alternative.id,
            character_id=alternative.character_id,
            conversation_id=conversation.id,
            content=alternative.content,
            created_at=alternative.created_at,
        )
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Could not save reply") from exc


@router.get("/history", response_model=list[HistoryMessage])
def history(
    request: Request,
    character_id: str = Query(default=DEFAULT_CHARACTER_ID, min_length=1, max_length=100),
    limit: int = Query(default=100, ge=1, le=200),
    conversation_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[Message]:
    if request.app.state.characters.get(character_id) is None:
        raise HTTPException(status_code=404, detail="Character not found")
    try:
        conversation = (
            db.get(Conversation, conversation_id)
            if conversation_id
            else latest_or_create(db, character_id)
        )
        if (
            conversation is None
            or conversation.is_deleted
            or conversation.character_id != character_id
        ):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return visible_messages(db, conversation)[-limit:]
    except HTTPException:
        raise
    except (SQLAlchemyError, ValueError):
        logger.exception("Could not load chat history")
        raise HTTPException(status_code=503, detail="Could not load chat history")


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request, payload: ChatRequest, db: Session = Depends(get_db)
) -> ChatResponse:
    if payload.attachments:
        raise HTTPException(status_code=422, detail="附件请使用流式聊天接口")
    character = request.app.state.characters.get(payload.character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")
    user_text = payload.message.strip()
    if not user_text:
        raise HTTPException(status_code=422, detail="Message cannot be blank")

    selected_service = request.app.state.model_roles.service("character_model")
    try:
        conversation = (
            db.get(Conversation, payload.conversation_id)
            if payload.conversation_id
            else latest_or_create(db, payload.character_id)
        )
        if (
            conversation is None
            or conversation.is_deleted
            or conversation.character_id != payload.character_id
        ):
            raise HTTPException(status_code=404, detail="Conversation not found")
        previous = visible_messages(db, conversation)
        reply_target = next(
            (row for row in previous if row.id == payload.reply_to_message_id), None
        )
        if payload.reply_to_message_id and (
            reply_target is None or reply_target.role != "assistant"
        ):
            raise HTTPException(status_code=404, detail="Reply target not found")
        history_messages = knowledge_chat.history(
            request.app, previous[-20:], conversation.id, payload.character_id, selected_service
        )
        memory = relationship_memory(db, payload.character_id, {item.id for item in previous[-20:]})
        overlay = read_settings(db, payload.character_id)
        manual = knowledge_chat.manual(
            request.app,
            relevant_manual(db, payload.character_id, user_text),
            conversation.id,
            payload.character_id,
            user_text,
        )
        user_row = Message(
            character_id=payload.character_id,
            conversation_id=conversation.id,
            reply_to_message_id=payload.reply_to_message_id,
            role="user",
            content=user_text,
        )
        db.add(user_row)
        touch_after_user_message(
            conversation, user_text, any(row.role == "user" for row in previous)
        )
        db.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not save user message")
        raise HTTPException(status_code=503, detail="Could not save message")

    messages = CharacterPromptBuilder().build(
        character,
        history_messages,
        user_text,
        memory_context=memory,
        reply_context=knowledge_chat.reply(
            request.app, reply_target, conversation.id, payload.character_id, selected_service
        ),
        personalization=overlay,
        manual_memory=manual,
    )
    assistant_id = str(uuid4())
    service = await knowledge_chat.grounded(
        request.app,
        selected_service,
        conversation.id,
        payload.character_id,
        user_text,
        assistant_id,
    )
    try:
        reply = await service.chat(messages)
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMAuthenticationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMResponseError as exc:
        logger.warning("Model request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Model request failed") from exc
    except Exception as exc:
        logger.exception("Unexpected model error")
        raise HTTPException(status_code=502, detail="Model request failed") from exc

    try:
        assistant_row = Message(
            id=assistant_id,
            character_id=payload.character_id,
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
        )
        db.add(assistant_row)
        db.commit()
        db.refresh(assistant_row)
        return ChatResponse(
            message_id=assistant_row.id,
            character_id=assistant_row.character_id,
            content=assistant_row.content,
            created_at=assistant_row.created_at,
            conversation_id=conversation.id,
        )
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not save assistant message")
        raise HTTPException(status_code=503, detail="Could not save reply")

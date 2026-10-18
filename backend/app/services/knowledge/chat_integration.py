"""Small opt-in hooks keep the original conversation and speech protocols intact."""

import asyncio

from app.services.knowledge.contracts import KnowledgeError


def owned_service(app, conversation_id, character_id):
    knowledge = getattr(app.state, "knowledge", None)
    if knowledge:
        from app.models.conversation import Conversation

        with knowledge.store.sessions() as db:
            row = db.get(Conversation, conversation_id)
            if row and row.character_id == character_id and not row.is_deleted:
                return knowledge
    return None


def history(app, rows, conversation_id, character_id):
    knowledge = owned_service(app, conversation_id, character_id)
    if knowledge:
        return knowledge.filter_history(
            rows, conversation_id, character_id, app.state.model_roles.service("character_model")
        )
    return [{"role": row.role, "content": row.content} for row in rows]


def manual(app, original, conversation_id, character_id, query):
    knowledge = owned_service(app, conversation_id, character_id)
    if not knowledge:
        return original
    if not knowledge.preference(conversation_id, character_id).get("memory_enabled", True):
        return []
    extra = knowledge.memory.relevant(character_id, conversation_id, query, include_character=False)
    return list(dict.fromkeys([*original, *extra]))[:8]


async def grounded(app, service, conversation_id, character_id, query, message_id):
    knowledge = owned_service(app, conversation_id, character_id)
    if knowledge:
        try:
            return await knowledge.chat_service(
                service, conversation_id, character_id, query, message_id
            )
        except KnowledgeError as error:
            from fastapi import HTTPException

            raise HTTPException(error.status, detail={"code": error.code}) from None
    return service

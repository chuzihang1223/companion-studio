"""Original Agent/Checkpoint integration; receipts contain identities, never bodies."""

import json

from sqlalchemy import select

from app.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.services.knowledge.context import GROUNDING_RULE, model_target
from app.services.knowledge.contracts import KnowledgeError

REFERENCE_KEYS = {"collection_id", "document_id", "document_version", "content_hash", "chunk_id"}


def references(result):
    if not isinstance(result, dict):
        return []
    rows = result.get("results", [])
    if isinstance(result.get("source"), dict):
        rows = [*rows, result["source"]]
    return [
        {key: row[key] for key in REFERENCE_KEYS}
        for row in rows[:20]
        if isinstance(row, dict) and REFERENCE_KEYS <= row.keys()
    ]


def snapshot(service, call, conversation_id, character_id):
    args, name = call["arguments"], call["name"]
    with service.store.sessions() as db:
        if name == "knowledge.search":
            ids = args["collection_ids"]
        elif name == "knowledge.collections":
            ids = [r.collection_id for r in service.store.grants(conversation_id, character_id)]
        else:
            chunk = (
                db.get(KnowledgeChunk, args["chunk_id"])
                if name.endswith("chunk")
                else db.scalar(
                    select(KnowledgeChunk).where(KnowledgeChunk.document_id == args["document_id"])
                )
            )
            if not chunk:
                raise KnowledgeError("KNOWLEDGE_DOCUMENT_NOT_FOUND", 404)
            ids = [chunk.collection_id]
        service.store.authorize(db, conversation_id, character_id, ids)
        grants = {
            r.collection_id: json.loads(r.model_targets_json)
            for r in service.store.grants(conversation_id, character_id)
        }
        return {
            "kind": "knowledge",
            "versions": [
                {
                    "collection_id": str(identity),
                    "revision": service.store.collection(db, identity).revision,
                    "index_version": service.store.collection(db, identity).index_version,
                    "targets": sorted(grants[str(identity)]),
                }
                for identity in sorted(ids)
            ],
        }


def validate_references(service, refs, conversation_id, character_id, target=None):
    return [
        service.retriever.citations.resolve(row, conversation_id, character_id, target=target)
        for row in refs
    ]


async def execute(app, context, call):
    import asyncio

    service = getattr(app.state, "knowledge", None)
    if service is None:
        raise KnowledgeError("KNOWLEDGE_UNAVAILABLE", 503)
    # Tool observations will be sent to the current character model at final
    # summarization. Local read permission alone cannot authorize that transfer.
    model = app.state.model_roles.service("character_model")
    proof = snapshot(service, call, context.conversation_id, context.character_id)
    with service.store.sessions() as db:
        service.store.authorize(
            db,
            context.conversation_id,
            context.character_id,
            [r["collection_id"] for r in proof["versions"]],
            model_target(model),
        )
    result = await asyncio.to_thread(
        service.tool, call["name"], call["arguments"], context.conversation_id, context.character_id
    )
    context.knowledge_tainted = True
    context.knowledge_references = [*(context.knowledge_references or []), *references(result)]
    if len(context.knowledge_references) > 80:
        raise KnowledgeError("KNOWLEDGE_CONTEXT_LIMIT")
    return result


def workflow_provenance(app, compiled, run, node):
    source_ids = {
        n.node_id
        for n in compiled.definition.nodes
        if n.type == "knowledge_search"
        or n.type == "tool"
        and str(n.config.get("name", "")).startswith("knowledge.")
    }
    selected = compiled.ancestors[node.node_id] & source_ids
    rows = [r for r in run["nodes"] if r["node_id"] in selected and r["state"] == "COMPLETED"]
    refs = [ref for row in rows for ref in references(row["output"])]
    if len(refs) > 80:
        raise KnowledgeError("KNOWLEDGE_CONTEXT_LIMIT")
    # Provenance comes from immutable graph ancestors and persisted node rows;
    # a transform cannot shed it by extracting just an excerpt string.
    return bool(rows), refs

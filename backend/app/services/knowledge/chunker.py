"""Stable content/location identities; source coordinates survive bounded splitting."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from app.services.knowledge.contracts import KnowledgeError


def chunk_document(document_id, blocks, *, size=1000, overlap=100, maximum=256):
    chunks = []
    for block in blocks:
        value, offset = block["text"], 0
        while offset < len(value):
            end = min(len(value), offset + size)
            if end < len(value):
                boundary = value.rfind("\n", offset + size // 2, end)
                if boundary > offset:
                    end = boundary + 1
            content = value[offset:end]
            location = dict(block["location"])
            location.update(character_start=offset, character_end=end)
            if "line_start" in location:
                location["line_start"] += value[:offset].count("\n")
                location["line_end"] = location["line_start"] + content.rstrip("\n").count("\n")
            digest = hashlib.sha256(content.encode()).hexdigest()
            identity = f"{document_id}:{digest}:{json.dumps(location, sort_keys=True)}"
            chunks.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, identity)),
                    "content": content,
                    "content_hash": digest,
                    "location": location,
                    "ordinal": len(chunks),
                }
            )
            if len(chunks) > maximum:
                raise KnowledgeError("KNOWLEDGE_DOCUMENT_CHUNK_LIMIT")
            if end == len(value):
                break
            offset = max(offset + 1, end - overlap)
    return chunks

"""Small SQLite metadata repository shared by MCP and Skills, bounded by kind."""

import json
from uuid import uuid4

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.extensions import ExtensionRecord
from app.services.mcp_ext.contracts import MCPError, bounded_json


class ExtensionStore:
    def __init__(self, factory=None):
        self.factory = factory or SessionLocal

    def listing(self, kind):
        with self.factory() as db:
            rows = db.scalars(
                select(ExtensionRecord).where(ExtensionRecord.kind == kind).limit(2001)
            )
            return [{"id": row.id, **json.loads(row.body)} for row in rows]

    def get(self, kind, key):
        with self.factory() as db:
            row = db.get(ExtensionRecord, key)
            if not row or row.kind != kind:
                raise MCPError("EXTENSION_NOT_FOUND", 404)
            return {"id": key, **json.loads(row.body)}

    def save(self, kind, body, key=None):
        body = dict(body)
        body.pop("id", None)
        bounded_json(body, limit=300000)
        key = key or str(uuid4())
        with self.factory() as db:
            row = db.get(ExtensionRecord, key)
            if row and row.kind != kind:
                raise MCPError("EXTENSION_CONFLICT")
            if not row:
                count = len(self.listing(kind))
                if count >= (40 if kind == "server" else 1000):
                    raise MCPError("EXTENSION_LIMIT")
                row = ExtensionRecord(id=key, kind=kind, body="{}")
                db.add(row)
            row.body = json.dumps(body, ensure_ascii=False)
            db.commit()
        return {"id": key, **body}

    def delete(self, kind, key):
        self.get(kind, key)
        with self.factory() as db:
            db.delete(db.get(ExtensionRecord, key))
            db.commit()

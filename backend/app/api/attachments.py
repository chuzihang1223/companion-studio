"""Serve only attachments referenced by a saved message."""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Message
from app.services.attachment_service import AttachmentService

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


@router.get("/{message_id}/{attachment_id}")
def attachment(message_id: str, attachment_id: str, db: Session = Depends(get_db)):
    try:
        UUID(message_id)
        if len(attachment_id) != 32 or any(char not in "0123456789abcdef" for char in attachment_id):
            raise ValueError("Invalid attachment id")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Attachment not found") from exc
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    item = next((row for row in json.loads(message.attachments_json or "[]") if row["id"] == attachment_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    path = AttachmentService().root / f"{attachment_id}{item['extension']}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment file missing")
    disposition = "inline" if item["kind"] == "image" else "attachment"
    return FileResponse(path, media_type=item["media_type"], filename=item["name"], content_disposition_type=disposition)

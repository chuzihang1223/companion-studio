"""Bound only newly generated voice files in this edition's dedicated cache."""

from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select

from app.core.config import settings
from app.models import MessageVoice

LIMIT = 128 * 1024 * 1024
TTL = timedelta(days=7)
NAME = re.compile(r"^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\.wav$")


def reserve(db, incoming: int, message_id: str):
    folder = settings.data_root / "voice_cache" / "realtime"
    if not folder.resolve().is_relative_to(settings.data_root.resolve()):
        raise ValueError("VOICE_CACHE_SCOPE_INVALID")
    folder.mkdir(parents=True, exist_ok=True)
    root = settings.data_root.resolve()
    if folder.is_symlink() or not folder.resolve().is_relative_to(root):
        raise ValueError("VOICE_CACHE_SCOPE_INVALID")
    entries, total = [], 0
    for index, path in enumerate(folder.iterdir()):
        if index >= 10000 or path.is_symlink() or not path.is_file():
            raise ValueError("VOICE_CACHE_SCOPE_INVALID")
        size = path.stat().st_size
        total += size
        if not NAME.fullmatch(path.name):
            continue
        row = db.get(MessageVoice, path.stem)
        relative = f"voice_cache/realtime/{path.name}"
        if row is not None and row.audio_path == relative and path.stem != message_id:
            entries.append((path.stat().st_mtime, size, path, row))
    cutoff = (datetime.now(timezone.utc) - TTL).timestamp()
    for modified, size, path, row in sorted(entries, key=lambda entry: entry[0]):
        if modified >= cutoff and total + incoming <= LIMIT:
            break
        try:
            path.unlink()
        except OSError:
            continue  # A playing file is not forcibly removed.
        row.status, row.audio_path = "missing", None
        total -= size
    db.flush()
    if incoming > LIMIT or total + incoming > LIMIT:
        raise ValueError("VOICE_CACHE_LIMIT")
    return folder

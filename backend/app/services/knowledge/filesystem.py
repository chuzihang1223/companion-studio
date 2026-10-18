"""Fail closed around explicitly selected roots, including Windows reparse points."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
from uuid import uuid4

from app.services.knowledge.contracts import KnowledgeError
from app.services.workspace_paths import scoped_path
from app.services.workspace_service import validate_root


def no_links(path: Path):
    for part in [path, *path.parents]:
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise KnowledgeError("KNOWLEDGE_REPARSE_POINT")


def root_record(value):
    original = Path(value)
    if not original.is_absolute():
        raise KnowledgeError("KNOWLEDGE_ROOT_REQUIRED")
    no_links(original)
    path = Path(validate_root(str(original)))
    info = path.stat()
    return {"id": str(uuid4()), "path": str(path), "device": info.st_dev, "inode": info.st_ino}


def checked_path(root, relative):
    lexical_parts = PurePosixPath(relative.replace("\\", "/"))
    if (
        lexical_parts.is_absolute()
        or ".." in lexical_parts.parts
        or ":" in relative
        or "\x00" in relative
        or len(relative) > 500
    ):
        raise KnowledgeError("KNOWLEDGE_PATH_OUTSIDE_ROOT", 403)
    base = Path(root["path"])
    no_links(base)
    info = base.stat()
    if (info.st_dev, info.st_ino) != (root["device"], root["inode"]):
        raise KnowledgeError("KNOWLEDGE_ROOT_CHANGED")
    # Check the lexical path before resolve(), otherwise a link inside the root
    # could resolve to a different in-root file without being noticed.
    lexical = base / relative
    no_links(lexical)
    return scoped_path(base, relative)


def opened_path(file, expected):
    if os.name != "nt":
        if (os.fstat(file.fileno()).st_dev, os.fstat(file.fileno()).st_ino) != (
            expected.stat().st_dev,
            expected.stat().st_ino,
        ):
            raise KnowledgeError("KNOWLEDGE_FILE_CHANGED")
        return
    import ctypes
    import msvcrt

    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
    api.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    api.restype = wintypes.DWORD
    buf = ctypes.create_unicode_buffer(32768)
    size = api(msvcrt.get_osfhandle(file.fileno()), buf, len(buf), 0)
    if not size or size >= len(buf):
        raise KnowledgeError("KNOWLEDGE_FILE_UNVERIFIABLE")
    actual = buf.value
    if actual.startswith("\\\\?\\UNC\\"):
        actual = "\\\\" + actual[8:]
    elif actual.startswith("\\\\?\\"):
        actual = actual[4:]
    if Path(actual) != expected:
        raise KnowledgeError("KNOWLEDGE_FILE_CHANGED")


def read_document(root, relative, maximum, *, after_read=None):
    try:
        path = checked_path(root, relative)
        with path.open("rb") as stream:
            opened_path(stream, path)
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise KnowledgeError("KNOWLEDGE_FILE_TOO_LARGE")
            raw = stream.read(maximum + 1)
            if after_read:
                after_read()
            after = os.fstat(stream.fileno())
            opened_path(stream, path)
            checked_path(root, relative)
        if len(raw) > maximum:
            raise KnowledgeError("KNOWLEDGE_FILE_TOO_LARGE")
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise KnowledgeError("KNOWLEDGE_FILE_CHANGED")
        return raw, {
            "size": len(raw),
            "mtime_ns": str(after.st_mtime_ns),
            "content_hash": hashlib.sha256(raw).hexdigest(),
        }
    except KnowledgeError:
        raise
    except (OSError, ValueError):
        raise KnowledgeError("KNOWLEDGE_SOURCE_UNAVAILABLE") from None

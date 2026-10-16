"""Internal Win32 long paths; this helper does not authorize file operations."""

import os
from pathlib import Path


def disk_path(value):
    """Keep system policy unchanged and preserve relative paths on other OSes.

    Callers must check ownership/reparse boundaries before deletion. The prefix
    is an IO detail, never a path to persist in exported metadata or a grant.
    """
    path = Path(value)
    if os.name != "nt" or not path.is_absolute():
        return path
    raw = os.path.abspath(path)
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    return Path("\\\\?\\UNC\\" + raw[2:] if raw.startswith("\\\\") else "\\\\?\\" + raw)

"""Keep selected Windows directories non-replaceable while enumerating metadata."""

from contextlib import contextmanager
import ctypes
import os
from pathlib import Path

from app.services.knowledge.filesystem import no_links
from app.services.scheduling.contracts import ScheduleError


@contextmanager
def pin_directory(path):
    path = Path(path)
    no_links(path)
    before = path.stat()
    handle = None
    if os.name == "nt":
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        api.CreateFileW.restype = wintypes.HANDLE
        api.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        # Windows exempts metadata-only handles from delete sharing checks.
        # FILE_LIST_DIRECTORY is required to actually block rename/replacement.
        handle = api.CreateFileW(str(path), 0x81, 3, None, 3, 0x02000000 | 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ScheduleError("SCHEDULE_SOURCE_UNAVAILABLE")
        buffer = ctypes.create_unicode_buffer(32768)
        size = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        final = buffer.value
        if final.startswith("\\\\?\\UNC\\"):
            final = "\\\\" + final[8:]
        elif final.startswith("\\\\?\\"):
            final = final[4:]
        if not size or size >= len(buffer) or Path(final) != path:
            api.CloseHandle(handle)
            raise ScheduleError("SCHEDULE_SOURCE_CHANGED")
    try:
        no_links(path)
        yield
        no_links(path)
        after = path.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ScheduleError("SCHEDULE_SOURCE_CHANGED")
    finally:
        if handle is not None:
            api.CloseHandle(handle)

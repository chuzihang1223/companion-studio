"""Read-only process identity prevents stealing a live scheduler after a wall-clock jump."""

import ctypes
import os
import sys


def process_identity(pid=None):
    pid = pid or os.getpid()
    if sys.platform != "win32":
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as stream:
                # Process names can contain spaces and parentheses.
                start = stream.read().rsplit(")", 1)[1].split()[19]
            return f"{pid}:{start}"
        except (OSError, IndexError):
            return f"{pid}:unverified"
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        # Access denied cannot prove a process dead; fail closed.
        return f"{pid}:unverified" if ctypes.get_last_error() == 5 else None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            return f"{pid}:unverified"
        born = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
        return f"{pid}:{born}"
    finally:
        kernel.CloseHandle(handle)


def owner_alive(identity):
    try:
        pid = int(identity.split(":", 1)[0])
        actual = process_identity(pid)
        return actual is not None and (actual == identity or actual.endswith(":unverified"))
    except (ValueError, OSError):
        return True

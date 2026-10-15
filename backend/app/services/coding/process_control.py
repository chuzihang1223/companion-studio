"""Suspend only the coding process and its descendants during a task pause."""

import ctypes
from ctypes import wintypes
import os


class ProcessTree:
    def __init__(self, pid):
        self.pid, self.handles = pid, []

    def pause(self):
        if os.name != "nt" or self.handles:
            return

        class Entry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD),
                ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD),
                ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD),
                ("name", wintypes.WCHAR * 260),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        relationships = []
        try:
            success = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while success:
                relationships.append((entry.pid, entry.parent))
                success = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        owned = {self.pid}
        for _ in range(20):
            more = {pid for pid, parent in relationships if parent in owned}
            if more <= owned:
                break
            owned |= more
        native = ctypes.WinDLL("ntdll")
        native.NtSuspendProcess.argtypes = [wintypes.HANDLE]
        for pid in [*sorted(owned - {self.pid}), self.pid]:
            handle = kernel.OpenProcess(0x800, False, pid)
            if handle:
                if native.NtSuspendProcess(handle) == 0:
                    self.handles.append(handle)
                else:
                    kernel.CloseHandle(handle)

    def resume(self):
        if not self.handles:
            return
        native = ctypes.WinDLL("ntdll")
        native.NtResumeProcess.argtypes = [wintypes.HANDLE]
        kernel = ctypes.WinDLL("kernel32")
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        for handle in reversed(self.handles):
            native.NtResumeProcess(handle)
            kernel.CloseHandle(handle)
        self.handles = []

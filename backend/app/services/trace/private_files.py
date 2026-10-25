"""Protect only the new, sanitized Trace journal; never change a profile's ACL."""

import os
from pathlib import Path
from app.core.filesystem import disk_path


def ordinary_path(path):
    path = Path(path)
    for item in (path, *path.parents):
        physical = disk_path(item)
        if physical.is_symlink() or physical.is_junction():
            raise OSError("TRACE_JOURNAL_LINK_REJECTED")
    physical = disk_path(path)
    if physical.is_file() and physical.stat().st_nlink != 1:
        raise OSError("TRACE_JOURNAL_LINK_REJECTED")
    return path


def private_directory(path):
    path = ordinary_path(path)
    disk_path(path).mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
        return
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    flags = win32con.CONTAINER_INHERIT_ACE | win32con.OBJECT_INHERIT_ACE
    for sid in (owner, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, win32con.GENERIC_ALL, sid)
    win32security.SetNamedSecurityInfo(
        str(disk_path(path)),
        win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


class JournalOwner:
    """An OS-owned lock survives Python cancellation and releases on process exit."""

    def __init__(self, path):
        self.file = disk_path(ordinary_path(path)).open("a+b")
        try:
            if self.file.seek(0, 2) == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.file.close()
            raise OSError("TRACE_JOURNAL_ALREADY_OWNED") from None

    def close(self):
        self.file.close()

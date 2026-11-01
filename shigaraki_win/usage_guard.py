"""Per-session Windows handle guard for one active Shigaraki edition.

Handles are references, not mutex ownership, so an EXE wrapper and its Python
child can both hold the lease. The per-edition marker permits a second launch
of that same edition to contact the existing local server.
"""
from __future__ import annotations

import ctypes

COMMON = "Local\\Shigaraki.Dictation.Active"
MARKERS = {"standard": "Local\\Shigaraki.Desktop.Running",
           "haas": "Local\\ShigarakiHaasPreview.Desktop.Running"}
SYNCHRONIZE = 0x00100000
ERROR_FILE_NOT_FOUND = 2
ERROR_ALREADY_EXISTS = 183


class OtherEditionRunning(RuntimeError):
    pass


def _windows_kernel():
    from ctypes import wintypes as W
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
    kernel.OpenMutexW.restype = W.HANDLE
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
    kernel.CreateMutexW.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.CloseHandle.restype = W.BOOL
    kernel.GetLastError.argtypes = []
    kernel.GetLastError.restype = W.DWORD
    kernel.SetLastError.argtypes = [W.DWORD]
    kernel.SetLastError.restype = None
    return kernel


def _present(kernel, name):
    handle = kernel.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        kernel.CloseHandle(handle)
        return True
    error = kernel.GetLastError()
    if error != ERROR_FILE_NOT_FOUND:
        raise OSError(error, "cannot check the Shigaraki usage guard")
    return False


class UsageGuard:
    def __init__(self, kernel, handles):
        self.kernel, self.handles = kernel, handles

    def close(self):
        handles, self.handles = self.handles, []
        for handle in reversed(handles):
            self.kernel.CloseHandle(handle)


def acquire(edition, *, kernel=None):
    if edition not in MARKERS:
        raise ValueError("unknown Shigaraki edition")
    kernel = kernel or _windows_kernel()
    own = MARKERS[edition]
    other = MARKERS["haas" if edition == "standard" else "standard"]
    # Also recognizes already-running a19/a1 wrappers, which predate COMMON.
    if _present(kernel, other):
        raise OtherEditionRunning(other)
    kernel.SetLastError(0)
    common = kernel.CreateMutexW(None, False, COMMON)
    existed = kernel.GetLastError() == ERROR_ALREADY_EXISTS
    if not common:
        raise OSError(kernel.GetLastError(), "cannot create the Shigaraki usage guard")
    try:
        if existed and not _present(kernel, own):
            raise OtherEditionRunning("another edition is starting")
        marker = kernel.CreateMutexW(None, False, own)
        if not marker:
            raise OSError(kernel.GetLastError(), "cannot create the edition marker")
    except BaseException:
        kernel.CloseHandle(common)
        raise
    return UsageGuard(kernel, [common, marker])

"""Windows loader status reporting; never changes an execution policy."""
import sys

POLICY_VIOLATION = 0xC0E90002


def policy_violation(code):
    return type(code) is int and (code & 0xFFFFFFFF) in (POLICY_VIOLATION, 4551)


def signed_exit_code(code):
    # CPython's exit status goes through a C int. Preserve all Windows NTSTATUS
    # bits without relying on conversion of an unsigned value above INT_MAX.
    return code - 2**32 if 2**31 <= code < 2**32 else code


class ProofreaderBlocked(RuntimeError):
    def __init__(self, code=POLICY_VIOLATION, *, cached=False):
        self.code, self.cached = code & 0xFFFFFFFF, cached
        super().__init__(f"proofreader blocked by Windows execution policy: 0x{self.code:08X}")


def loader_errors_as_status():
    """Child-process loader errors return status codes instead of modal dialogs."""
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetErrorMode.restype = ctypes.c_uint
        kernel.SetErrorMode.argtypes = [ctypes.c_uint]
        kernel.SetErrorMode.restype = ctypes.c_uint
        # Inherited by the native child. This changes error presentation only;
        # blocked DLLs remain blocked and are not retried through another loader.
        kernel.SetErrorMode(kernel.GetErrorMode() | 0x0001 | 0x0002 | 0x8000)

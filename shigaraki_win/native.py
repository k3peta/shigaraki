from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes as W
from dataclasses import dataclass


@dataclass
class Target:
    hwnd: int
    pid: int
    element: object
    runtime_id: tuple
    value: str
    selection: object
    prefix: str
    suffix: str
    app: str
    paste_mode: str = "verified"
    value_source: str = ""
    site_eligible: bool = False


CLIPBOARD_APPS = frozenset({"antigravity.exe", "chrome.exe", "chatgpt.exe"})
SITE_BROWSERS = frozenset({"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"})


def uia_flag(value, expected):
    # UIA BOOL properties are native 32-bit integers in some COM wrappers.
    # TextRange attributes, in contrast, are VARIANTs and keep their own check.
    return type(value) in (bool, int) and value == int(expected)


def normalize_newlines(text: str) -> str:
    # UIA ValuePattern and TextPattern may expose CRLF and LF for the same field.
    # Do not strip spaces or normalize characters: those changes are meaningful.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def may_restore(target_hwnd: int, target_pid: int, foreground_hwnd: int, foreground_pid: int, own_pid: int) -> bool:
    return bool(target_hwnd and target_pid and (
        (foreground_hwnd == target_hwnd and foreground_pid == target_pid) or foreground_pid == own_pid
    ))


class WindowsInput:
    """Only reads the focused editable UIA field. No document/tree searches."""

    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows input is only available on Windows")
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA
        self.UIA = UIA
        self.automation = comtypes.client.CreateObject("{FF48DBA4-60EF-4201-AA87-54103EEF594E}", interface=UIA.IUIAutomation)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = W.HWND
        self.user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
        self.user.GetWindowTextW.restype = ctypes.c_int
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
        self.user.GetWindowThreadProcessId.restype = W.DWORD
        self.user.SetForegroundWindow.argtypes = [W.HWND]
        self.user.SetForegroundWindow.restype = W.BOOL
        self.user.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user.GetAsyncKeyState.restype = ctypes.c_short
        self.user.GetClipboardSequenceNumber.restype = W.DWORD

    def foreground(self):
        hwnd = self.user.GetForegroundWindow()
        pid = W.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(hwnd or 0), int(pid.value)

    def key_down(self, key):
        return bool(self.user.GetAsyncKeyState(key) & 0x8000)

    def window_title(self, hwnd, pid):
        if self.foreground() != (hwnd, pid) or pid == os.getpid():
            return ""
        title = ctypes.create_unicode_buffer(161)
        self.user.GetWindowTextW(hwnd, title, len(title))
        return title.value if self.foreground() == (hwnd, pid) else ""

    def foreground_page(self):
        from .page_context import BROWSERS
        hwnd, pid = self.foreground()
        if not hwnd or pid == os.getpid() or self.app_name(pid).casefold() not in BROWSERS:
            return None
        return self.window_title(hwnd, pid)

    def _site_automation_client(self):
        if hasattr(self, "_site_automation"):
            return self._site_automation
        self._site_automation = None
        try:
            import comtypes.client
            client = comtypes.client.CreateObject(
                "{E22AD333-B25F-460C-83D0-0581107395C9}", interface=self.UIA.IUIAutomation2)
            client.ConnectionTimeout = 50
            client.TransactionTimeout = 50
            if client.ConnectionTimeout == 50 and client.TransactionTimeout == 50:
                self._site_automation = client
        except Exception:
            pass
        return self._site_automation

    def _site_window_title(self, target):
        if not may_restore(target.hwnd, target.pid, *self.foreground(), os.getpid()):
            return None
        pid = W.DWORD()
        if not self.user.GetWindowThreadProcessId(target.hwnd, ctypes.byref(pid)) or pid.value != target.pid:
            return None
        title = ctypes.create_unicode_buffer(161)
        if self.user.GetWindowTextW(target.hwnd, title, len(title)) <= 0:
            return None
        return title.value if may_restore(target.hwnd, target.pid, *self.foreground(), os.getpid()) else None

    def site_host(self, target):
        """Optional metadata only: caller probes once when an enabled site option exists."""
        from .page_context import excluded
        if (not isinstance(target, Target) or target.site_eligible is not True or
                target.app.casefold() not in SITE_BROWSERS or not target.runtime_id):
            return ""
        try:
            title = self._site_window_title(target)
            if title is None or excluded(title) or self.app_name(target.pid).casefold() != target.app.casefold():
                return ""
            client = self._site_automation_client()
            if client is None:
                return ""
            deadline = time.monotonic() + .05
            def request(function, *args):
                if time.monotonic() >= deadline:
                    raise TimeoutError
                value = function(*args)
                if time.monotonic() >= deadline:
                    raise TimeoutError
                return value
            def property_value(element, name):
                return request(getattr, element, name)
            if self.foreground() == (target.hwnd, target.pid):
                field = request(client.GetFocusedElement)
                if (tuple(request(field.GetRuntimeId)) != target.runtime_id or
                        property_value(field, "CurrentProcessId") != target.pid or
                        not uia_flag(property_value(field, "CurrentIsPassword"), False) or
                        not uia_flag(property_value(field, "CurrentHasKeyboardFocus"), True)):
                    return ""
            walker = property_value(client, "RawViewWalker")
            # No property calls on the old client's captured proxy. The short-timeout
            # walker creates parent elements; never enumerate children or siblings.
            node = target.element
            for _ in range(12):
                if time.monotonic() >= deadline:
                    return ""
                node = request(walker.GetParentElement, node)
                if (not node or property_value(node, "CurrentProcessId") != target.pid or
                        not uia_flag(property_value(node, "CurrentIsPassword"), False)):
                    return ""
                if property_value(node, "CurrentControlType") != self.UIA.UIA_DocumentControlTypeId:
                    continue
                # A writable compose box can also be Document. Do not inspect its
                # value, or climb past an unknown inner document to another site.
                if property_value(node, "CurrentAutomationId") != "RootWebArea":
                    return ""
                host = ""
                try:
                    unknown = request(node.GetCurrentPattern, self.UIA.UIA_LegacyIAccessiblePatternId)
                    pattern = request(unknown.QueryInterface, self.UIA.IUIAutomationLegacyIAccessiblePattern)
                    state = property_value(pattern, "CurrentState")
                    if type(state) is int and state & 0x20000000:
                        return ""
                    if property_value(pattern, "CurrentRole") == 15 and type(state) is int and state & 0x40:
                        host = self._http_site_host(property_value(pattern, "CurrentValue"))
                except Exception:
                    pass
                if not host and time.monotonic() < deadline:
                    try:
                        unknown = request(node.GetCurrentPattern, self.UIA.UIA_ValuePatternId)
                        pattern = request(unknown.QueryInterface, self.UIA.IUIAutomationValuePattern)
                        if uia_flag(property_value(pattern, "CurrentIsReadOnly"), True):
                            host = self._http_site_host(property_value(pattern, "CurrentValue"))
                    except Exception:
                        pass
                return host if (time.monotonic() < deadline and self._site_window_title(target) == title) else ""
        except Exception:
            # Provider errors can contain a URL or text. Retain neither the error
            # message nor intermediate values in diagnostics or settings.
            pass
        return ""

    @staticmethod
    def _http_site_host(value):
        from .context_profiles import normalize_host
        if not isinstance(value, str) or len(value) > 4096 or not value.lower().startswith(("https://", "http://")):
            return ""
        return normalize_host(value)

    def modifiers_up(self):
        return not any(self.key_down(k) for k in [0xA2, 0xA3, 0xA4, 0xA5, 0xA0, 0xA1, 0x5B, 0x5C])

    def editable_document(self, field):
        pattern = field.GetCurrentPattern(self.UIA.UIA_TextPatternId).QueryInterface(self.UIA.IUIAutomationTextPattern)
        document = pattern.DocumentRange
        original = document.GetText(-1)
        try:
            value = field.GetCurrentPattern(self.UIA.UIA_ValuePatternId).QueryInterface(self.UIA.IUIAutomationValuePattern)
        except Exception:
            # Rich browser editors may expose TextPattern without ValuePattern.
            # Require a focused Edit control with an explicitly writable range;
            # never infer editability from a page/document or an unknown value.
            if (field.CurrentControlType != self.UIA.UIA_EditControlTypeId or
                    document.GetAttributeValue(self.UIA.UIA_IsReadOnlyAttributeId) is not False):
                raise ValueError("not a verifiable editable range")
        else:
            if not uia_flag(value.CurrentIsReadOnly, False):
                raise ValueError("read-only field")
            content, exposed = normalize_newlines(original), normalize_newlines(str(value.CurrentValue))
            # Text ranges can include one virtual trailing paragraph terminator.
            if content not in (exposed, exposed + "\n"):
                raise ValueError("inconsistent text providers")
        return pattern, document, original

    def clipboard_snapshot(self, field):
        """Read one known editable control, without requiring its selection API."""
        if not uia_flag(field.CurrentIsKeyboardFocusable, True):
            raise ValueError("not keyboard focusable")
        legacy = None
        try:
            legacy = field.GetCurrentPattern(self.UIA.UIA_LegacyIAccessiblePatternId).QueryInterface(
                self.UIA.IUIAutomationLegacyIAccessiblePattern)
        except Exception:
            pass
        if legacy is not None and (type(legacy.CurrentState) is not int or
                legacy.CurrentState & (0x1 | 0x40 | 0x20000000)):
            raise ValueError("protected or read-only legacy control")
        # A Document may be a compose box or an entire page. A writable Value
        # is explicit evidence of editability. Without it require the legacy
        # TEXT role and interactive state; a TextPattern alone is not enough.
        legacy_edit = bool(legacy is not None and legacy.CurrentRole == 42
                           and legacy.CurrentState & 0x100000
                           and not legacy.CurrentState & (0x1 | 0x40 | 0x20000000))
        try:
            value = field.GetCurrentPattern(self.UIA.UIA_ValuePatternId).QueryInterface(
                self.UIA.IUIAutomationValuePattern)
        except Exception:
            value = None
        if value is not None and not uia_flag(value.CurrentIsReadOnly, False):
            raise ValueError("read-only or unknown value")
        if (field.CurrentControlType != self.UIA.UIA_EditControlTypeId and not (
                field.CurrentControlType == self.UIA.UIA_DocumentControlTypeId and
                (value is not None or legacy_edit))):
            raise ValueError("not an editable control")
        if value is not None:
            original, source = value.CurrentValue, "value"
        else:
            try:
                pattern = field.GetCurrentPattern(self.UIA.UIA_TextPatternId).QueryInterface(
                    self.UIA.IUIAutomationTextPattern)
            except Exception:
                pattern = None
            if pattern is not None:
                if pattern.DocumentRange.GetAttributeValue(self.UIA.UIA_IsReadOnlyAttributeId) is not False:
                    raise ValueError("read-only or unknown range")
                original, source = pattern.DocumentRange.GetText(100001), "text"
            elif legacy_edit:
                original, source = legacy.CurrentValue, "legacy"
            else:
                raise ValueError("no writable text provider")
        if not isinstance(original, str) or len(original) > 100000:
            raise ValueError("unavailable or oversized editor value")
        return original, source

    def capture(self):
        self.last_target_reason = "foreground"
        self.last_capture_checks = {}
        self.last_capture_fallback = ""
        self.last_capture_error = ""
        hwnd, pid = self.foreground()
        if not hwnd or pid == os.getpid():
            return None
        try:
            self.last_target_reason = "focused_element"
            field = self.automation.GetFocusedElement()
            process_matches = field.CurrentProcessId == pid
            password, enabled, focused = field.CurrentIsPassword, field.CurrentIsEnabled, field.CurrentHasKeyboardFocus
            self.last_capture_checks = {"same_process": process_matches, "password": bool(password),
                                        "enabled": bool(enabled), "keyboard_focus": bool(focused)}
            if not process_matches or password or not enabled or not focused:
                self.last_target_reason = "protected_or_unfocused"
                return None
            app = self.app_name(pid)
            if app.casefold() != "antigravity.exe":
                try:
                    # Keep exact selection and context for capable providers.
                    # A browser's Value-only field is still a valid paste target.
                    return self.capture_document(field, hwnd, pid, app)
                except Exception as error:
                    self.last_capture_error = type(error).__name__
                    if app.casefold() not in CLIPBOARD_APPS:
                        return None
                    self.last_capture_fallback = self.last_target_reason
            self.last_target_reason = "clipboard_editable"
            original, source = self.clipboard_snapshot(field)
            runtime = tuple(field.GetRuntimeId())
            if not runtime or not all(type(part) is int for part in runtime):
                self.last_target_reason = "clipboard_identity"
                return None
            self.last_target_reason = "captured_clipboard_" + source
            return Target(hwnd, pid, field, runtime, original, None, "", "", app,
                          paste_mode="clipboard", value_source=source,
                          site_eligible=app.casefold() in SITE_BROWSERS)
        except Exception as error:
            # Never put COM error messages or editor contents into diagnostics.
            self.last_capture_error = type(error).__name__
            return None

    def capture_document(self, field, hwnd, pid, app):
        self.last_target_reason = "editable_document"
        pattern, document, original = self.editable_document(field)
        self.last_target_reason = "selection"
        ranges = pattern.GetSelection()
        if ranges.Length != 1:
            raise ValueError("selection unavailable")
        selected = ranges.GetElement(0).Clone()
        prefix = document.Clone()
        prefix.MoveEndpointByRange(1, selected, 0)
        suffix = document.Clone()
        suffix.MoveEndpointByRange(0, selected, 1)
        target = Target(hwnd, pid, field, tuple(field.GetRuntimeId()), original, selected,
                        prefix.GetText(-1), suffix.GetText(-1), app,
                        site_eligible=app.casefold() in SITE_BROWSERS)
        self.last_target_reason = "captured"
        return target

    def unchanged(self, target: Target):
        try:
            field = target.element
            if field.CurrentProcessId != target.pid or field.CurrentIsPassword or not field.CurrentIsEnabled:
                return False
            if getattr(target, "paste_mode", "verified") == "clipboard":
                if (target.app.casefold() not in CLIPBOARD_APPS or
                        tuple(field.GetRuntimeId()) != target.runtime_id):
                    return False
                original, _ = self.clipboard_snapshot(field)
                return normalize_newlines(original) == normalize_newlines(target.value)
            pattern, _, original = self.editable_document(field)
            selected = pattern.GetSelection()
            return (normalize_newlines(original) == normalize_newlines(target.value) and
                    selected.Length == 1 and target.selection.CompareEndpoints(0, selected.GetElement(0), 0) == 0 and
                    target.selection.CompareEndpoints(1, selected.GetElement(0), 1) == 0)
        except Exception:
            return False

    def prepare_paste(self, target):
        hwnd, pid = self.foreground()
        self.last_prepare_reason = "foreground_changed"
        if not may_restore(target.hwnd, target.pid, hwnd, pid, os.getpid()):
            return False
        self.last_prepare_reason = "modifier_down"
        if not self.modifiers_up():
            return False
        self.last_prepare_reason = "field_changed"
        if not self.unchanged(target):
            return False
        if hwnd != target.hwnd:
            self.user.SetForegroundWindow(target.hwnd)
        current = self.capture()
        self.last_prepare_reason = "focus_changed"
        ready = bool(current and current.hwnd == target.hwnd and current.pid == target.pid and
                     current.runtime_id == target.runtime_id and self.unchanged(target))
        if ready:
            self.last_prepare_reason = "ready"
        return ready

    def send_paste(self, target=None):
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class INPUTUNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ("data",)
            _fields_ = [("type", W.DWORD), ("data", INPUTUNION)]
        events = (INPUT * 4)()
        for event, key, flags in zip(events, [0x11, 0x56, 0x56, 0x11], [0, 0, 2, 2]):
            event.type = 1
            event.ki = KEYBDINPUT(key, 0, flags, 0, 0x53484947)
        self.user.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user.SendInput.restype = W.UINT
        # Check again after constructing the native events, as close as possible
        # to dispatch. Foreground keyboard injection cannot be tied to a HWND.
        if target is not None and (self.foreground() != (target.hwnd, target.pid) or not self.modifiers_up()):
            return False
        return self.user.SendInput(4, events, ctypes.sizeof(INPUT)) == 4

    def paste_receipt(self, target: Target, text: str) -> bool | None:
        """True: exact receipt; False: still unchanged; None: cannot verify.

        Read only the previously captured field. Selection/caret providers often
        lag after paste, so do not run the pre-paste capture checks again here.
        A changed/formatted or inaccessible result is not evidence of failure.
        """
        # The compatibility route deliberately did not capture a selection.
        # Dispatch success is not proof of insertion; never fabricate a receipt
        # or retry a paste whose application-side result cannot be verified.
        if getattr(target, "paste_mode", "verified") == "clipboard":
            return None
        try:
            if self.foreground() != (target.hwnd, target.pid):
                return None
            field = target.element
            if (field.CurrentProcessId != target.pid or field.CurrentIsPassword or
                    not field.CurrentIsEnabled or not field.CurrentHasKeyboardFocus or
                    tuple(field.GetRuntimeId()) != target.runtime_id):
                return None
            values = []
            try:
                value = field.GetCurrentPattern(self.UIA.UIA_ValuePatternId).QueryInterface(self.UIA.IUIAutomationValuePattern)
                values.append(normalize_newlines(str(value.CurrentValue)))
            except Exception:
                pass
            try:
                pattern = field.GetCurrentPattern(self.UIA.UIA_TextPatternId).QueryInterface(self.UIA.IUIAutomationTextPattern)
                values.append(normalize_newlines(pattern.DocumentRange.GetText(-1)))
            except Exception:
                pass
            expected = normalize_newlines(target.prefix + text + target.suffix)
            if expected in values:
                return True
            if values and all(value == normalize_newlines(target.value) for value in values):
                return False
            return None
        except Exception:
            return None

    def app_name(self, pid):
        # Executable basename only; never retain the full executable path.
        from pathlib import Path
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        kernel.OpenProcess.restype = W.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            name = ctypes.create_unicode_buffer(32768)
            size = W.DWORD(len(name))
            return Path(name.value).name if kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)) else ""
        finally:
            kernel.CloseHandle(handle)

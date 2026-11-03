"""Bounded browser metadata probes; no live COM, desktop, or private content."""
from types import ModuleType, SimpleNamespace
import os
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.native import Target, WindowsInput


class CapturedProxy:
    def __getattr__(self, name):
        raise AssertionError("must not read a property through the old UIA client")


class SiteHostTests(unittest.TestCase):
    def setUp(self):
        self.native = WindowsInput.__new__(WindowsInput)
        self.native.UIA = SimpleNamespace(UIA_DocumentControlTypeId=4,
            UIA_LegacyIAccessiblePatternId=5, IUIAutomationLegacyIAccessiblePattern=object(),
            UIA_ValuePatternId=1, IUIAutomationValuePattern=object(), IUIAutomation2=object())
        self.target = Target(101, 202, CapturedProxy(), (7, 8), "", None, "", "", "chrome.exe",
                             site_eligible=True)
        self.native.foreground = MagicMock(return_value=(101, 202))
        self.native.app_name = MagicMock(return_value="chrome.exe")
        self.native._site_window_title = MagicMock(return_value="A page - Chrome")
        self.field = SimpleNamespace(GetRuntimeId=lambda: (7, 8), CurrentProcessId=202,
                                     CurrentIsPassword=False, CurrentHasKeyboardFocus=True)
        self.client = SimpleNamespace(GetFocusedElement=MagicMock(return_value=self.field),
            RawViewWalker=SimpleNamespace(GetParentElement=MagicMock()))
        self.native._site_automation_client = MagicMock(return_value=self.client)

    def node(self, *, kind=4, marker="RootWebArea", pid=202, legacy=None, value=None):
        node = SimpleNamespace(CurrentProcessId=pid, CurrentIsPassword=False,
                               CurrentControlType=kind, CurrentAutomationId=marker)
        providers = {k: v for k, v in ((5, legacy), (1, value)) if v is not None}
        def pattern(key):
            if key not in providers:
                raise RuntimeError("pattern unavailable")
            return SimpleNamespace(QueryInterface=lambda interface: providers[key])
        node.GetCurrentPattern = MagicMock(side_effect=pattern)
        return node

    def root(self, url="https://EXAMPLE.com/path?secret=fixture", *, state=0x40):
        return self.node(legacy=SimpleNamespace(CurrentRole=15, CurrentState=state, CurrentValue=url))

    def test_chromium_metadata_returns_only_normalized_host(self):
        self.client.RawViewWalker.GetParentElement.side_effect = [self.node(kind=3), self.root()]
        self.assertEqual(self.native.site_host(self.target), "example.com")
        self.assertEqual(self.client.RawViewWalker.GetParentElement.call_count, 2)

    def test_value_pattern_fallback_uses_read_only_document(self):
        root = self.node(value=SimpleNamespace(CurrentIsReadOnly=1, CurrentValue="https://chatgpt.com/x"))
        self.client.RawViewWalker.GetParentElement.return_value = root
        self.assertEqual(self.native.site_host(self.target), "chatgpt.com")

    def test_editor_document_value_is_never_inspected_or_skipped(self):
        inner = self.node(marker="compose-box")
        self.client.RawViewWalker.GetParentElement.side_effect = [inner, self.root()]
        self.assertEqual(self.native.site_host(self.target), "")
        inner.GetCurrentPattern.assert_not_called()
        self.assertEqual(self.client.RawViewWalker.GetParentElement.call_count, 1)

    def test_unknown_inner_frame_never_borrows_outer_origin(self):
        self.client.RawViewWalker.GetParentElement.side_effect = [self.root("about:blank"), self.root()]
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertEqual(self.client.RawViewWalker.GetParentElement.call_count, 1)

    def test_writable_or_protected_document_value_is_not_read(self):
        reads = []
        class Value:
            CurrentIsReadOnly = False
            @property
            def CurrentValue(self):
                reads.append("value")
                raise AssertionError("must not read an editor value")
        class Legacy:
            CurrentRole = 15
            CurrentState = 0x20000040
            @property
            def CurrentValue(self):
                reads.append("legacy")
                raise AssertionError("must not read a protected value")
        self.client.RawViewWalker.GetParentElement.return_value = self.node(legacy=Legacy(), value=Value())
        self.assertEqual(self.native.site_host(self.target), "")
        self.client.RawViewWalker.GetParentElement.return_value = self.node(value=Value())
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertEqual(reads, [])

    def test_protected_legacy_state_does_not_fall_back_to_another_provider(self):
        root = self.node(legacy=SimpleNamespace(CurrentRole=15, CurrentState=0x20000040),
                         value=SimpleNamespace(CurrentIsReadOnly=True, CurrentValue="https://example.com"))
        self.client.RawViewWalker.GetParentElement.return_value = root
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertEqual(root.GetCurrentPattern.call_count, 1)

    def test_password_and_changed_field_skip_ancestor_walk(self):
        for field in (SimpleNamespace(GetRuntimeId=lambda: (99,), CurrentProcessId=202),
                      SimpleNamespace(GetRuntimeId=lambda: (7, 8), CurrentProcessId=202,
                                      CurrentIsPassword=True, CurrentHasKeyboardFocus=True)):
            self.client.GetFocusedElement.return_value = field
            self.assertEqual(self.native.site_host(self.target), "")
        self.client.RawViewWalker.GetParentElement.assert_not_called()

    def test_main_panel_uses_known_capture_without_recapturing_own_field(self):
        self.native.foreground.return_value = (303, os.getpid())
        self.client.RawViewWalker.GetParentElement.return_value = self.root()
        self.assertEqual(self.native.site_host(self.target), "example.com")
        self.client.GetFocusedElement.assert_not_called()

    def test_private_title_and_missing_eligibility_never_create_client(self):
        self.native._site_window_title.return_value = "Private browsing - Chrome"
        self.assertEqual(self.native.site_host(self.target), "")
        self.native._site_window_title.return_value = "ordinary"
        self.target.site_eligible = False
        self.assertEqual(self.native.site_host(self.target), "")
        self.native._site_automation_client.assert_not_called()

    def test_non_chromium_and_unrecognized_target_never_create_client(self):
        self.target.app = "firefox.exe"
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertEqual(self.native.site_host(None), "")
        self.native._site_automation_client.assert_not_called()

    def test_wrong_process_ancestor_and_changed_window_fail_closed(self):
        self.client.RawViewWalker.GetParentElement.return_value = self.node(pid=999)
        self.assertEqual(self.native.site_host(self.target), "")
        self.client.RawViewWalker.GetParentElement.return_value = self.root()
        self.native._site_window_title.side_effect = ["ordinary", None]
        self.assertEqual(self.native.site_host(self.target), "")

    def test_walk_is_bounded_without_any_descendant_enumeration(self):
        self.client.RawViewWalker.GetParentElement.return_value = self.node(kind=3)
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertEqual(self.client.RawViewWalker.GetParentElement.call_count, 12)

    def test_expired_rpc_budget_does_not_issue_another_request(self):
        clock = [100.0]
        root = self.root()
        def slow_parent(node):
            clock[0] += .051
            return root
        self.client.RawViewWalker.GetParentElement.side_effect = slow_parent
        with patch("shigaraki_win.native.time.monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(self.native.site_host(self.target), "")
        root.GetCurrentPattern.assert_not_called()

    def test_provider_error_is_not_retained_as_diagnostics(self):
        self.client.RawViewWalker.GetParentElement.side_effect = RuntimeError("https://fixture.test/private")
        self.assertEqual(self.native.site_host(self.target), "")
        self.assertNotIn("fixture.test", repr(self.native.__dict__))

    def test_full_url_validation_rejects_credentials_and_non_web_values(self):
        for value in ("example.com", "file:///fixture", "https://user:secret@example.com/x", "https://example.com\\other", "https://" + "x" * 4096):
            self.assertEqual(WindowsInput._http_site_host(value), "")
        self.assertEqual(WindowsInput._http_site_host("https://sub.EXAMPLE.com.:443/a?q=b"), "sub.example.com")


class SiteClientTests(unittest.TestCase):
    def test_separate_client_requires_confirmed_short_timeouts_and_is_reused(self):
        native = WindowsInput.__new__(WindowsInput)
        native.UIA = SimpleNamespace(IUIAutomation2=object())
        client = SimpleNamespace()
        package, module = ModuleType("comtypes"), ModuleType("comtypes.client")
        module.CreateObject = MagicMock(return_value=client)
        package.client = module
        with patch.dict("sys.modules", {"comtypes": package, "comtypes.client": module}):
            self.assertIs(native._site_automation_client(), client)
            self.assertIs(native._site_automation_client(), client)
        self.assertEqual((client.ConnectionTimeout, client.TransactionTimeout), (50, 50))
        module.CreateObject.assert_called_once()


    def test_unconfigurable_client_is_never_used_or_retried(self):
        native = WindowsInput.__new__(WindowsInput)
        native.UIA = SimpleNamespace(IUIAutomation2=object())
        class BadClient:
            def __setattr__(self, name, value):
                raise RuntimeError("unsupported")
        package, module = ModuleType("comtypes"), ModuleType("comtypes.client")
        module.CreateObject = MagicMock(return_value=BadClient())
        package.client = module
        with patch.dict("sys.modules", {"comtypes": package, "comtypes.client": module}):
            self.assertIsNone(native._site_automation_client())
            self.assertIsNone(native._site_automation_client())
        module.CreateObject.assert_called_once()


class SiteWindowTests(unittest.TestCase):
    def setUp(self):
        self.native = WindowsInput.__new__(WindowsInput)
        self.target = Target(101, 202, object(), (7, 8), "", None, "", "", "chrome.exe", site_eligible=True)
        self.native.foreground = MagicMock(return_value=(101, 202))
        self.pid, self.alive = 202, True
        def window_pid(hwnd, result):
            result._obj.value = self.pid
            return int(self.alive)
        def window_title(hwnd, buffer, size):
            buffer.value = "Fixture page"
            return len(buffer.value)
        self.native.user = SimpleNamespace(GetWindowThreadProcessId=window_pid, GetWindowTextW=window_title)

    def test_target_and_main_panel_foreground_are_allowed(self):
        self.assertEqual(self.native._site_window_title(self.target), "Fixture page")
        self.native.foreground.return_value = (303, os.getpid())
        self.assertEqual(self.native._site_window_title(self.target), "Fixture page")

    def test_reused_or_gone_hwnd_and_unrelated_foreground_are_refused(self):
        self.pid = 999
        self.assertIsNone(self.native._site_window_title(self.target))
        self.pid, self.alive = 202, False
        self.assertIsNone(self.native._site_window_title(self.target))
        self.alive = True
        self.native.foreground.return_value = (303, 404)
        self.assertIsNone(self.native._site_window_title(self.target))

    def test_unavailable_window_title_is_not_assumed_safe(self):
        self.native.user.GetWindowTextW = lambda *args: 0
        self.assertIsNone(self.native._site_window_title(self.target))



if __name__ == "__main__":
    unittest.main()

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from shigaraki_win.native import WindowsInput
from shigaraki_win.context import ShortContext


class ClipboardCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.native = WindowsInput.__new__(WindowsInput)
        self.native.foreground = MagicMock(return_value=(101, 202))
        self.native.modifiers_up = MagicMock(return_value=True)
        self.native.app_name = MagicMock(return_value="Antigravity.exe")
        self.native.UIA = SimpleNamespace(UIA_EditControlTypeId=3, UIA_DocumentControlTypeId=4,
            UIA_ValuePatternId=1, UIA_TextPatternId=2, UIA_LegacyIAccessiblePatternId=5,
            UIA_IsReadOnlyAttributeId=6, IUIAutomationValuePattern=object(),
            IUIAutomationTextPattern=object(), IUIAutomationLegacyIAccessiblePattern=object())
        self.field = MagicMock()
        self.field.CurrentProcessId = 202
        self.field.CurrentIsPassword = False
        self.field.CurrentIsEnabled = True
        self.field.CurrentHasKeyboardFocus = True
        self.field.CurrentIsKeyboardFocusable = True
        self.field.CurrentControlType = 3
        self.field.GetRuntimeId.return_value = (7, 8)
        self.value = SimpleNamespace(CurrentValue="入力前", CurrentIsReadOnly=False)
        self.document = MagicMock()
        self.document.GetText.return_value = "入力前"
        self.document.GetAttributeValue.return_value = False
        self.pattern = SimpleNamespace(DocumentRange=self.document, GetSelection=MagicMock())
        self.legacy = SimpleNamespace(CurrentRole=42, CurrentState=0x100000, CurrentValue="入力前")
        self.providers = {1: self.value}
        def query(key):
            if key not in self.providers:
                raise RuntimeError("pattern unsupported")
            result = MagicMock()
            result.QueryInterface.return_value = self.providers[key]
            return result
        self.field.GetCurrentPattern.side_effect = query
        self.native.automation = SimpleNamespace(GetFocusedElement=lambda: self.field)

    def test_value_only_editor_captures_and_prepares_without_selection_api(self):
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertEqual(target.paste_mode, "clipboard")
        self.assertEqual(target.value_source, "value")
        self.assertIsNone(target.selection)
        self.assertTrue(self.native.prepare_paste(target))
        self.assertEqual(self.native.last_prepare_reason, "ready")
        self.assertIsNone(self.native.paste_receipt(target, "認識結果"))
        self.pattern.GetSelection.assert_not_called()

    def test_different_text_provider_and_missing_selection_do_not_block_compatible_editor(self):
        self.providers[2] = self.pattern
        self.document.GetText.return_value = "可視本文を含む別の表現"
        self.pattern.GetSelection.side_effect = RuntimeError("no selection provider")
        target = self.native.capture()
        self.assertEqual(target.value, "入力前")
        self.assertTrue(self.native.prepare_paste(target))
        self.pattern.GetSelection.assert_not_called()
        self.document.GetText.assert_not_called()

    def test_text_only_edit_can_use_writable_range_without_getselection(self):
        self.providers = {2: self.pattern}
        target = self.native.capture()
        self.assertEqual(target.value_source, "text")
        self.assertTrue(self.native.prepare_paste(target))
        self.pattern.GetSelection.assert_not_called()
        self.document.GetText.assert_called_with(100001)

    def test_provider_becoming_available_is_allowed_only_for_same_value(self):
        self.providers = {2: self.pattern}
        target = self.native.capture()
        self.providers[1] = self.value
        self.assertTrue(self.native.prepare_paste(target))
        self.value.CurrentValue = "変更後"
        self.assertFalse(self.native.prepare_paste(target))

    def test_document_compose_box_requires_explicit_legacy_text_role(self):
        self.field.CurrentControlType = 4
        self.providers = {2: self.pattern}
        self.assertIsNone(self.native.capture())
        self.providers[5] = self.legacy
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertTrue(self.native.prepare_paste(target))
        self.legacy.CurrentRole = 15  # ROLE_SYSTEM_DOCUMENT: a page is not a textbox.
        self.assertIsNone(self.native.capture())

    def test_legacy_only_textbox_keeps_writable_and_value_guard(self):
        self.providers = {5: self.legacy}
        target = self.native.capture()
        self.assertEqual(target.value_source, "legacy")
        self.assertTrue(self.native.prepare_paste(target))
        self.legacy.CurrentValue = "本人が編集した本文"
        self.assertFalse(self.native.prepare_paste(target))

    def test_password_readonly_unknown_and_disabled_are_rejected(self):
        for attribute, value in (("CurrentIsPassword", True), ("CurrentIsEnabled", False),
                                 ("CurrentHasKeyboardFocus", False), ("CurrentProcessId", 999),
                                 ("CurrentIsKeyboardFocusable", None)):
            with self.subTest(attribute=attribute):
                original = getattr(self.field, attribute)
                setattr(self.field, attribute, value)
                self.assertIsNone(self.native.capture())
                setattr(self.field, attribute, original)
        for value in (True, 1, None, object(), "false"):
            self.value.CurrentIsReadOnly = value
            self.assertIsNone(self.native.capture())

    def test_native_bool_properties_accept_only_zero_and_one(self):
        self.field.CurrentIsKeyboardFocusable = 1
        self.value.CurrentIsReadOnly = 0
        self.assertIsNotNone(self.native.capture())
        for value in (2, -1, "true", object()):
            self.field.CurrentIsKeyboardFocusable = value
            self.assertIsNone(self.native.capture())

    def test_legacy_readonly_unavailable_or_protected_is_rejected(self):
        self.providers = {5: self.legacy}
        for state in (0, 0x100040, 0x100001, 0x20100000):
            self.legacy.CurrentState = state
            self.assertIsNone(self.native.capture())

    def test_writable_value_does_not_override_protected_legacy_state(self):
        self.providers[5] = self.legacy
        for state in (0x100040, 0x100001, 0x20100000, None):
            self.legacy.CurrentState = state
            self.assertIsNone(self.native.capture())

    def test_text_readonly_variant_unknown_does_not_fall_back_to_legacy(self):
        self.providers = {2: self.pattern, 5: self.legacy}
        for value in (True, 0, None, object()):
            self.document.GetAttributeValue.return_value = value
            self.assertIsNone(self.native.capture())

    def test_other_apps_do_not_get_compatible_route(self):
        for app in ("editor.exe", "powershell.exe", "not-antigravity.exe", "not-chrome.exe"):
            self.native.app_name.return_value = app
            self.assertIsNone(self.native.capture())  # Value-only remains unsupported there.

    def test_chrome_and_chatgpt_value_only_fields_can_fall_back_to_clipboard(self):
        # A14 stops at editable_document even though the field is focused,
        # enabled, not a password and exposes a writable ValuePattern.
        for app in ("chrome.exe", "ChatGPT.exe"):
            with self.subTest(app=app):
                self.native.app_name.return_value = app
                target = self.native.capture()
                self.assertIsNotNone(target)
                self.assertEqual(target.paste_mode, "clipboard")
                self.assertEqual(target.value_source, "value")
                self.assertEqual(self.native.last_capture_fallback, "editable_document")
                self.assertTrue(self.native.prepare_paste(target))

    def test_chrome_keeps_verified_route_when_text_and_selection_are_available(self):
        self.native.app_name.return_value = "chrome.exe"
        self.providers[2] = self.pattern
        self.pattern.GetSelection.return_value.Length = 1
        selected = self.pattern.GetSelection.return_value.GetElement.return_value
        selected.Clone.return_value = selected
        selected.CompareEndpoints.return_value = 0
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertEqual(target.paste_mode, "verified")
        self.assertEqual(self.native.last_capture_fallback, "")
        self.assertTrue(self.native.prepare_paste(target))

    def test_chrome_missing_selection_can_fall_back_but_only_in_same_field(self):
        self.native.app_name.return_value = "chrome.exe"
        self.providers[2] = self.pattern
        self.pattern.GetSelection.return_value.Length = 0
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertEqual(target.paste_mode, "clipboard")
        self.assertEqual(self.native.last_capture_fallback, "selection")
        self.assertTrue(self.native.prepare_paste(target))
        self.field.GetRuntimeId.return_value = (9, 10)
        self.assertFalse(self.native.prepare_paste(target))

    def test_writable_value_document_does_not_require_legacy_selection_provider(self):
        self.field.CurrentControlType = 4
        for app in ("Antigravity.exe", "chrome.exe", "ChatGPT.exe"):
            with self.subTest(app=app):
                self.native.app_name.return_value = app
                target = self.native.capture()
                self.assertIsNotNone(target)
                self.assertEqual(target.paste_mode, "clipboard")
                self.assertTrue(self.native.prepare_paste(target))

    def test_chrome_fallback_rejects_protected_or_unknown_fields(self):
        self.native.app_name.return_value = "chrome.exe"
        for attribute, value in (("CurrentIsPassword", True), ("CurrentIsEnabled", False),
                                 ("CurrentHasKeyboardFocus", False), ("CurrentProcessId", 999),
                                 ("CurrentIsKeyboardFocusable", None), ("CurrentControlType", 99)):
            with self.subTest(attribute=attribute):
                original = getattr(self.field, attribute)
                setattr(self.field, attribute, value)
                self.assertIsNone(self.native.capture())
                setattr(self.field, attribute, original)
        for value in (True, None, object(), "false"):
            self.value.CurrentIsReadOnly = value
            self.assertIsNone(self.native.capture())

    def test_fallback_does_not_capture_unknown_text_only_document(self):
        self.native.app_name.return_value = "chrome.exe"
        self.field.CurrentControlType = 4
        self.providers = {2: self.pattern}
        self.assertIsNone(self.native.capture())

    def test_compatible_target_stays_valid_if_exact_provider_later_appears(self):
        self.native.app_name.return_value = "chrome.exe"
        target = self.native.capture()
        self.providers[2] = self.pattern
        self.pattern.GetSelection.return_value.Length = 1
        self.assertTrue(self.native.prepare_paste(target))
        self.assertIsNone(self.native.paste_receipt(target, "本文"))

    def test_changed_value_runtime_foreground_modifiers_or_focus_prevent_paste(self):
        target = self.native.capture()
        self.value.CurrentValue = "変更後"
        self.assertFalse(self.native.prepare_paste(target))
        self.value.CurrentValue = target.value
        self.field.GetRuntimeId.return_value = (99,)
        self.assertFalse(self.native.prepare_paste(target))
        self.field.GetRuntimeId.return_value = target.runtime_id
        self.native.foreground.return_value = (999, 202)
        self.assertFalse(self.native.prepare_paste(target))
        self.native.foreground.return_value = (101, 202)
        self.native.modifiers_up.return_value = False
        self.assertFalse(self.native.prepare_paste(target))
        self.native.modifiers_up.return_value = True
        self.field.CurrentHasKeyboardFocus = False
        self.assertFalse(self.native.prepare_paste(target))
        self.field.CurrentHasKeyboardFocus = True
        self.value.CurrentIsReadOnly = True
        self.assertFalse(self.native.prepare_paste(target))

    def test_invalid_identity_or_oversized_value_is_not_captured(self):
        self.field.GetRuntimeId.return_value = ()
        self.assertIsNone(self.native.capture())
        self.field.GetRuntimeId.return_value = (7, 8)
        self.value.CurrentValue = "x" * 100001
        self.assertIsNone(self.native.capture())

    def test_dispatch_checks_foreground_and_modifiers_again_without_retrying(self):
        target = self.native.capture()
        self.native.user = SimpleNamespace(SendInput=MagicMock(return_value=4))
        self.native.foreground.return_value = (999, 202)
        self.assertFalse(self.native.send_paste(target))
        self.native.user.SendInput.assert_not_called()
        self.native.foreground.return_value = (101, 202)
        self.native.modifiers_up.return_value = False
        self.assertFalse(self.native.send_paste(target))
        self.native.user.SendInput.assert_not_called()
        self.native.modifiers_up.return_value = True
        self.assertTrue(self.native.send_paste(target))
        self.native.user.SendInput.assert_called_once()
        self.assertEqual(self.native.user.SendInput.call_args.args[0], 4)

    def test_compatible_target_never_supplies_context_or_short_memory(self):
        target = self.native.capture()
        context = ShortContext()
        self.assertEqual(context.context_for(target), "")
        context.remember(target, "送った文章")
        self.assertEqual(context._recent, "")
        self.assertIsNone(context._key)


if __name__ == "__main__":
    unittest.main()

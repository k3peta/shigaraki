from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from shigaraki_win.native import Target, WindowsInput


class PasteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.native = WindowsInput.__new__(WindowsInput)
        self.native.foreground = MagicMock(return_value=(101, 202))
        self.native.UIA = SimpleNamespace(UIA_ValuePatternId=1, UIA_TextPatternId=2,
            IUIAutomationValuePattern=object(), IUIAutomationTextPattern=object(),
            UIA_EditControlTypeId=3, UIA_IsReadOnlyAttributeId=4)
        self.field = MagicMock()
        self.field.CurrentProcessId = 202
        self.field.CurrentIsPassword = False
        self.field.CurrentIsEnabled = True
        self.field.CurrentHasKeyboardFocus = True
        self.field.CurrentControlType = 3
        self.field.GetRuntimeId.return_value = (7, 8)
        self.value = SimpleNamespace(CurrentValue="前\n本文\n後", CurrentIsReadOnly=False)
        self.document = MagicMock()
        self.document.GetText.return_value = "前\n本文\n後"
        self.pattern = SimpleNamespace(DocumentRange=self.document)
        def get_pattern(key):
            result = MagicMock()
            result.QueryInterface.return_value = self.value if key == 1 else self.pattern
            return result
        self.field.GetCurrentPattern.side_effect = get_pattern
        self.target = Target(101, 202, self.field, (7, 8), "前\n後", None,
                             "前\n", "後", "editor.exe")

    def test_matching_readback_accepts_windows_newlines(self):
        self.value.CurrentValue = "前\r\n本文\r\n後"
        self.assertIs(self.native.paste_receipt(self.target, "本文\n"), True)

    def test_capture_accepts_only_line_ending_differences(self):
        self.native.automation = SimpleNamespace(GetFocusedElement=lambda: self.field)
        self.native.app_name = MagicMock(return_value="editor.exe")
        self.pattern.GetSelection = MagicMock()
        self.pattern.GetSelection.return_value.Length = 1
        self.value.CurrentValue = "前\r\n本文\r\n後"
        self.assertIsNotNone(self.native.capture())
        self.value.CurrentValue = "前\n本 文\n後"
        self.assertIsNone(self.native.capture())

    def test_pre_paste_guard_still_checks_text_and_selection(self):
        self.target.selection = MagicMock()
        self.target.selection.CompareEndpoints.return_value = 0
        self.pattern.GetSelection = MagicMock()
        self.pattern.GetSelection.return_value.Length = 1
        self.value.CurrentValue = "前\r\n後"
        self.document.GetText.return_value = self.target.value
        self.assertTrue(self.native.unchanged(self.target))
        self.value.CurrentValue = "別の本文"
        self.assertFalse(self.native.unchanged(self.target))
        self.value.CurrentValue = self.target.value
        self.target.selection.CompareEndpoints.return_value = 1
        self.assertFalse(self.native.unchanged(self.target))

    def text_only_capture(self):
        self.native.automation = SimpleNamespace(GetFocusedElement=lambda: self.field)
        self.native.app_name = MagicMock(return_value="chrome.exe")
        self.pattern.GetSelection = MagicMock()
        self.pattern.GetSelection.return_value.Length = 1
        selection = self.pattern.GetSelection.return_value.GetElement.return_value
        selection.Clone.return_value = selection
        selection.CompareEndpoints.return_value = 0
        get_pattern = self.field.GetCurrentPattern.side_effect
        def text_only(key):
            if key == 1:
                raise RuntimeError("no ValuePattern")
            return get_pattern(key)
        self.field.GetCurrentPattern.side_effect = text_only
        self.document.GetAttributeValue.return_value = False

    def test_browser_text_only_editable_field_keeps_exact_selection_guard(self):
        self.text_only_capture()
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertTrue(self.native.unchanged(target))
        self.document.GetText.return_value = "本人が変更した文"
        self.assertFalse(self.native.unchanged(target))

    def test_text_only_unknown_readonly_page_and_password_are_rejected(self):
        self.text_only_capture()
        for value in (True, object(), 0, None):
            self.document.GetAttributeValue.return_value = value
            self.assertIsNone(self.native.capture())
        self.document.GetAttributeValue.return_value = False
        self.field.CurrentControlType = 99
        self.assertIsNone(self.native.capture())
        self.field.CurrentControlType = 3
        self.field.CurrentIsPassword = True
        self.assertIsNone(self.native.capture())

    def test_virtual_document_newline_is_accepted_without_dropping_real_text(self):
        self.native.automation = SimpleNamespace(GetFocusedElement=lambda: self.field)
        self.native.app_name = MagicMock(return_value="chrome.exe")
        self.pattern.GetSelection = MagicMock()
        self.pattern.GetSelection.return_value.Length = 1
        self.document.GetText.return_value = self.value.CurrentValue + "\r\n"
        target = self.native.capture()
        self.assertIsNotNone(target)
        self.assertEqual(target.value, self.document.GetText.return_value)
        self.value.CurrentIsReadOnly = True
        self.assertIsNone(self.native.capture())

    def test_readback_does_not_require_recapturing_selection(self):
        self.native.capture = MagicMock(side_effect=RuntimeError("provider selection is stale"))
        self.assertIs(self.native.paste_receipt(self.target, "本文\n"), True)
        self.native.capture.assert_not_called()

    def test_text_provider_can_confirm_when_value_pattern_is_unavailable(self):
        get_pattern = self.field.GetCurrentPattern.side_effect
        def text_only(key):
            if key == 1:
                raise RuntimeError("provider unavailable")
            return get_pattern(key)
        self.field.GetCurrentPattern.side_effect = text_only
        self.assertIs(self.native.paste_receipt(self.target, "本文\n"), True)

    def test_only_an_unchanged_readable_field_is_negative_evidence(self):
        self.value.CurrentValue = self.target.value
        self.document.GetText.return_value = self.target.value
        self.assertIs(self.native.paste_receipt(self.target, "本文\n"), False)

    def test_app_formatting_and_disagreeing_providers_remain_unverified(self):
        self.value.CurrentValue = "前\n本文 後"
        self.document.GetText.return_value = self.target.value
        self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))

    def test_unknown_is_not_a_failed_paste(self):
        self.field.GetCurrentPattern.side_effect = RuntimeError("UIA temporarily unavailable")
        self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))

    def test_changed_window_focus_or_identity_cannot_confirm(self):
        for attribute, value in [("CurrentProcessId", 999), ("CurrentIsPassword", True),
                                 ("CurrentHasKeyboardFocus", False), ("CurrentIsEnabled", False)]:
            original = getattr(self.field, attribute)
            setattr(self.field, attribute, value)
            self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))
            setattr(self.field, attribute, original)
        self.field.GetRuntimeId.return_value = (9, 9)
        self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))
        self.field.GetRuntimeId.return_value = (7, 8)
        self.native.foreground.return_value = (999, 202)
        self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))

    def test_non_newline_whitespace_is_not_normalized_into_success(self):
        self.value.CurrentValue = "前\n本 文\n後"
        self.document.GetText.return_value = self.value.CurrentValue
        self.assertIsNone(self.native.paste_receipt(self.target, "本文\n"))


if __name__ == "__main__":
    unittest.main()

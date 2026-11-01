import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


# The probe's unknown_reserved classification also covered valid navigation and
# ABNT keys. Keep those valid keys protected; ignore only reserved/unassigned VKs.
RESERVED_UNASSIGNED = (
    0x00, 0x07, 0x0A, 0x0B, 0x0E, 0x0F, 0x3A, 0x3B, 0x3C, 0x3D,
    0x3E, 0x3F, 0x40, 0x5E, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C,
    0x9D, 0x9E, 0x9F, 0xB8, 0xB9, 0xE0, 0xE8, 0xFF,
)


class HotkeyIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["Shigaraki hotkey fixture"])

    def setUp(self):
        from shigaraki_win.app import App
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.keys = set()
        self.patches = [
            patch("shigaraki_win.app.WindowsInput"),
            patch("shigaraki_win.app.QSystemTrayIcon"),
            patch("shigaraki_win.app.data_dir", return_value=self.root),
            patch("shigaraki_win.app.default_model", return_value=self.root / "fixture.gguf"),
            patch("shigaraki_win.app.CpuEngine"),
            patch("shigaraki_win.app.ProofreadRuntime"),
            patch("shigaraki_win.app.Recorder"),
            patch("shigaraki_win.app.CaptionSession"),
            patch("sounddevice.query_devices", return_value=[]),
        ]
        for item in self.patches:
            item.start()
        self.app = App(self.qt)
        self.initial_modifier = self.app.main.modifier.currentIndex()
        # These legacy Ctrl-named cases explicitly select Ctrl.
        self.app.main.modifier.setCurrentIndex(0)
        self.app.timer.stop()
        (self.root / "fixture.gguf").write_bytes(b"fixture; never read by a model")
        self.app.settings["setup_complete"] = True
        self.app.setup.hide()
        self.app.setup.ready = True
        self.app.main.hide()
        self.app.engine.ready = True
        self.app.proof_engine.ready = False
        self.app.native.capture.return_value = None
        self.app.native.foreground.return_value = (101, 999999)
        self.app.native.key_down.side_effect = lambda key: key in self.keys
        self.app.save_stream_diagnostics = MagicMock()
        from shigaraki_win import app as module
        module.Recorder.return_value.level_percent = 0
        module.Recorder.return_value.recorded_seconds = 0
        module.CaptionSession.return_value.started_at = 100.0
        module.CaptionSession.return_value.metrics = {"first_caption_seconds": None}
        module.CaptionSession.return_value.done.is_set.return_value = False
        self.recorder = module.Recorder.return_value
        self.session = module.CaptionSession.return_value

    def tearDown(self):
        self.app.timer.stop()
        self.app.main.hide()
        self.app.panel.hide()
        self.app.setup.hide()
        self.qt.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temporary.cleanup()

    def tick(self, now):
        with patch("shigaraki_win.app.time.monotonic", return_value=now):
            self.app.tick()

    def hold(self, key=0xA2, start=100.0):
        self.keys = {key, 0x11 if key in (0xA2, 0xA3) else 0x12}
        self.tick(start)
        self.tick(start + .30)

    def assert_recording_with_small_window(self):
        self.assertEqual(self.app.phase, "recording")
        self.recorder.start.assert_called_once()
        self.assertTrue(self.app.panel.isVisible())
        self.assertFalse(self.app.main.presented())
        self.assertFalse(self.app.setup.isVisible())

    def test_haas_defaults_to_control_but_alt_remains_selectable(self):
        self.assertEqual(self.initial_modifier, 0)
        self.reset_attempt(1)
        self.hold(0xA2)
        self.recorder.start.assert_not_called()
        self.reset_attempt(1)
        self.hold(0xA4)
        self.assert_recording_with_small_window()

    def double_tap(self, key, start):
        generic = 0x11 if key == 0xA2 else 0x12
        self.keys = {key, generic}; self.tick(start)
        self.keys = set(); self.tick(start+.08)
        self.keys = {key, generic}; self.tick(start+.20)
        self.keys = set(); self.tick(start+.28)

    def test_alt_double_tap_opens_and_closes_main_without_recording(self):
        self.reset_attempt(1)
        self.double_tap(0xA4, 100.0)
        self.assertTrue(self.app.main.presented())
        self.double_tap(0xA4, 101.0)
        self.assertFalse(self.app.main.presented())
        self.recorder.start.assert_not_called()
        self.assertEqual(self.app.phase, "idle")

    def test_default_control_double_tap_opens_and_closes_main_without_recording(self):
        self.assertEqual(self.initial_modifier, 0)
        self.reset_attempt(0)
        self.double_tap(0xA2, 100.0)
        self.assertTrue(self.app.main.presented())
        self.double_tap(0xA2, 101.0)
        self.assertFalse(self.app.main.presented())
        self.recorder.start.assert_not_called()

    def test_control_double_tap_is_ignored_when_alt_is_selected(self):
        self.reset_attempt(1)
        self.double_tap(0xA2, 100.0)
        self.assertFalse(self.app.main.presented())
        self.recorder.start.assert_not_called()

    def test_ctrl_hold_recovers_from_a_completed_setup_window_left_visible(self):
        self.app.setup.show()
        self.hold()
        self.assert_recording_with_small_window()

    def test_alt_hold_recovers_from_a_completed_setup_window_left_visible(self):
        self.app.main.modifier.setCurrentIndex(1)
        self.app.setup.show()
        self.hold(0xA4)
        self.assert_recording_with_small_window()

    def test_show_main_dismisses_completed_setup_then_native_close_allows_ctrl_hold(self):
        self.app.setup.show()
        self.app.show_main()
        self.assertTrue(self.app.main.presented())
        self.assertFalse(self.app.setup.isVisible())
        self.app.main.close()
        self.assertFalse(self.app.main.presented())
        self.hold()
        self.assert_recording_with_small_window()

    def test_direct_start_dismisses_completed_setup_for_mouse_recording(self):
        self.app.setup.show()
        self.app.start(False)
        self.assert_recording_with_small_window()

    def test_main_window_keeps_small_window_hidden_for_ctrl_recording(self):
        self.app.setup.show()
        self.app.show_main()
        self.hold()
        self.assertEqual(self.app.phase, "recording")
        self.assertTrue(self.app.continuous)
        self.recorder.start.assert_called_once()
        self.assertFalse(self.app.panel.isVisible())

    def test_incomplete_setup_still_blocks_hotkey_recording(self):
        self.app.setup.ready = False
        self.app.setup.show()
        self.hold()
        self.recorder.start.assert_not_called()
        self.assertEqual(self.app.phase, "idle")
        self.assertFalse(self.app.panel.isVisible())

    def test_preparation_phase_still_blocks_hotkey_even_with_completed_setup(self):
        self.app.phase = "modelSetup"
        self.app.setup.show()
        self.hold()
        self.recorder.start.assert_not_called()
        self.assertEqual(self.app.phase, "modelSetup")

    def test_ctrl_c_never_starts_recording(self):
        self.keys = {0xA2, 0x11, 0x43}
        self.tick(100.0)
        self.tick(100.30)
        self.keys = {0xA2, 0x11}
        self.tick(100.60)
        self.recorder.start.assert_not_called()
        self.assertEqual(self.app.phase, "idle")

    def test_right_ctrl_does_not_start_left_ctrl_recording(self):
        self.hold(0xA3)
        self.recorder.start.assert_not_called()
        self.assertEqual(self.app.phase, "idle")

    def test_other_key_during_an_active_hold_does_not_stop_recording(self):
        self.hold()
        self.keys.add(0x43)
        self.tick(100.60)
        self.keys.discard(0x43)
        self.tick(100.90)
        self.assertEqual(self.app.phase, "recording")
        self.session.stop.assert_not_called()

    def diagnostic_calls(self, writer):
        return [call for call in writer.call_args_list
                if Path(call.args[0]).name == "hotkey-diagnostics.json"]

    def test_diagnostics_have_no_text_and_are_written_once_for_each_hold(self):
        self.app.main.editor.setPlainText("非公開の入力本文です。")
        self.app.raw_text = "非公開の認識本文です。"
        self.app.setup.show()
        with patch.object(self.app, "write_json", wraps=self.app.write_json) as writer:
            self.hold()
            for now in (100.60, 100.90, 101.20):
                self.tick(now)
            calls = self.diagnostic_calls(writer)
            self.assertEqual(len(calls), 1)
        saved = json.loads((self.root / "hotkey-diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {
            "version", "phase", "setup_visible", "setup_ready", "selected_modifier",
            "isolated", "blocked", "accepted", "input_ready",
            "navigation_down", "abnt_down", "other_valid_down",
        })
        self.assertEqual(saved["phase"], "idle")
        self.assertEqual(saved["selected_modifier"], 0)
        self.assertTrue(saved["accepted"])
        for key in ("setup_visible", "setup_ready", "isolated", "blocked", "accepted", "input_ready",
                    "navigation_down", "abnt_down", "other_valid_down"):
            self.assertIs(type(saved[key]), bool)
        self.assertNotIn("非公開", json.dumps(saved, ensure_ascii=False))

    def test_blocked_hold_is_diagnosed_once_and_a_new_press_is_a_new_attempt(self):
        self.app.setup.ready = False
        self.app.setup.show()
        with patch.object(self.app, "write_json", wraps=self.app.write_json) as writer:
            self.hold()
            self.tick(100.60)
            self.assertEqual(len(self.diagnostic_calls(writer)), 1)
            self.keys = set()
            self.tick(100.70)
            self.hold(start=101.0)
            self.assertEqual(len(self.diagnostic_calls(writer)), 2)
        saved = json.loads((self.root / "hotkey-diagnostics.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["setup_visible"])
        self.assertFalse(saved["setup_ready"])
        self.assertTrue(saved["blocked"])
        self.assertFalse(saved["accepted"])

    def test_brief_keyup_jitter_does_not_duplicate_diagnosis_or_stop_recording(self):
        with patch.object(self.app, "write_json", wraps=self.app.write_json) as writer:
            self.hold()
            self.keys = set()
            self.tick(100.35)
            self.keys = {0xA2, 0x11}
            self.tick(100.38)
            self.tick(100.70)
            self.assertEqual(len(self.diagnostic_calls(writer)), 1)
        self.assertEqual(self.app.phase, "recording")
        self.session.stop.assert_not_called()

    def reset_attempt(self, modifier):
        from shigaraki_win.gestures import ControlGesture
        self.app.phase = "idle"
        self.app.recorder = None
        self.app.session = None
        self.app.proofreader = None
        self.app.control = ControlGesture()
        self.app.alt = ControlGesture()
        self.app._shortcut_diagnosed.clear()
        self.app.main.hide()
        self.app.panel.hide()
        self.app.setup.hide()
        self.app.main.modifier.setCurrentIndex(modifier)
        self.recorder.start.reset_mock()
        self.session.stop.reset_mock()

    def test_reserved_states_do_not_block_control_or_alt_holds(self):
        for modifier, activation, generic in ((0, 0xA2, 0x11), (1, 0xA4, 0x12)):
            for reserved in RESERVED_UNASSIGNED:
                with self.subTest(modifier=modifier, reserved=reserved):
                    self.reset_attempt(modifier)
                    self.keys = {activation, generic, reserved}
                    self.tick(100.0)
                    self.tick(100.30)
                    self.assert_recording_with_small_window()

    def test_valid_key_chords_remain_blocked_for_both_recording_modifiers(self):
        conflicts = (0x43, 0xA0, 0xA1, 0xA3, 0xA5, 0x01, 0x02, 0x04,
                     0x15, 0x16, 0x1A, 0x1C, 0x1D, 0xE5, 0xE7, 0xF3,
                     0xC3, 0x70, 0x25, *range(0x88, 0x90), 0xC1, 0xC2)
        for modifier, activation, generic in ((0, 0xA2, 0x11), (1, 0xA4, 0x12)):
            for conflict in conflicts:
                with self.subTest(modifier=modifier, conflict=conflict):
                    self.reset_attempt(modifier)
                    self.keys = {activation, generic, conflict}
                    self.tick(100.0)
                    self.tick(100.30)
                    self.keys.discard(conflict)
                    self.tick(100.60)
                    self.recorder.start.assert_not_called()
                    self.assertEqual(self.app.phase, "idle")

    def test_control_and_alt_chord_does_not_start_either_modifier(self):
        for modifier in (0, 1):
            with self.subTest(modifier=modifier):
                self.reset_attempt(modifier)
                self.keys = {0xA2, 0x11, 0xA4, 0x12}
                self.tick(100.0)
                self.tick(100.30)
                self.recorder.start.assert_not_called()
                self.assertEqual(self.app.phase, "idle")

    def test_diagnostic_conflict_categories_are_booleans_without_input_text(self):
        cases = ((set(), (False, False, False)),
                 ({0x88}, (True, False, False)),
                 ({0xC1}, (False, True, False)),
                 ({0x43}, (False, False, True)),
                 ({0x88, 0xC1, 0x43}, (True, True, True)))
        names = ("navigation_down", "abnt_down", "other_valid_down")
        for conflicts, expected in cases:
            with self.subTest(conflicts=conflicts):
                self.reset_attempt(0)
                self.app.raw_text = "非公開の認識本文です。"
                self.app.main.editor.setPlainText("非公開の入力本文です。")
                self.keys = {0xA2, 0x11} | conflicts
                self.tick(100.0)
                self.tick(100.30)
                saved = json.loads((self.root / "hotkey-diagnostics.json").read_text(encoding="utf-8"))
                self.assertEqual(tuple(saved[name] for name in names), expected)
                for name in names:
                    self.assertIs(type(saved[name]), bool)
                self.assertNotIn("非公開", json.dumps(saved, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

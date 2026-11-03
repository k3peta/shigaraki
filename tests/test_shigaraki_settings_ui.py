import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from test_shigaraki_startup import RegistryFixture
from shigaraki_win.startup import LoginStartup


class SettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["Settings fixture"])

    def setUp(self):
        from shigaraki_win.app import App
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.launcher = self.root / "信楽.exe"
        self.launcher.write_bytes(b"fixture")
        self.registry = RegistryFixture()
        self.startup = LoginStartup(self.launcher, registry=self.registry)
        patches = [
            patch("shigaraki_win.app.WindowsInput"),
            patch("shigaraki_win.app.QSystemTrayIcon"),
            patch("shigaraki_win.app.LoginStartup", return_value=self.startup),
            patch("shigaraki_win.app.data_dir", return_value=self.root),
            patch("shigaraki_win.app.default_model", return_value=self.root / "missing.gguf"),
            patch("sounddevice.query_devices", return_value=[]),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.app = App(self.qt, force_setup=True)
        self.app.timer.stop()
        self.addCleanup(self.dispose)

    def dispose(self):
        for window in (self.app.main, self.app.panel, self.app.setup):
            window.hide()
            window.deleteLater()

    def test_initial_settings_and_refresh_do_not_write_startup(self):
        self.assertFalse(self.app.main.launch_at_login.isChecked())
        self.startup.set_enabled(True)
        self.registry.writes.clear()
        self.app.refresh_login_startup()
        self.assertTrue(self.app.main.launch_at_login.isChecked())
        self.assertEqual(self.registry.writes, [])

    def test_checkbox_changes_registration_and_failure_restores_checked_state(self):
        self.app.main.launch_at_login.setChecked(True)
        self.assertTrue(self.startup.registered())
        self.registry.fail_write = True
        self.app.main.launch_at_login.setChecked(False)
        self.assertTrue(self.app.main.launch_at_login.isChecked())
        self.assertIn("変更できませんでした", self.app.main.startup_status.text())
        self.assertTrue(self.startup.registered())

    def test_read_failure_is_visible_without_registration_write(self):
        with patch.object(self.startup, "registered", side_effect=PermissionError("fixture")):
            self.app.refresh_login_startup()
        self.assertFalse(self.app.main.launch_at_login.isEnabled())
        self.assertIn("確認できませんでした", self.app.main.startup_status.text())
        self.assertEqual(self.registry.writes, [])

    def test_quit_button_retains_edited_idle_draft_and_is_idempotent(self):
        self.app.main.editor.setPlainText("終了前の手直しした文章。")
        self.app.main.quit_button.click()
        self.assertTrue(self.app.quitting)
        saved = json.loads((self.root / "last-input.json").read_text())
        self.assertEqual(saved["text"], "終了前の手直しした文章。")
        self.assertEqual(saved["outcome"], "draft")
        self.app.persist_edited_draft = MagicMock()
        self.app.quit()
        self.app.persist_edited_draft.assert_not_called()
        self.app.native.paste.assert_not_called()

    def test_quit_cancels_recording_or_finishing_without_pasting(self):
        for phase in ("recording", "finishing"):
            with self.subTest(phase=phase):
                self.app.quitting = False
                self.app.phase = phase
                self.app.commit_requested = True
                self.app.cancel_recording = MagicMock()
                self.app.session = MagicMock()
                self.app.proofreader = MagicMock()
                self.app.main.quit_button.click()
                self.assertTrue(self.app.quitting)
                self.app.session.cancel.assert_called_once()
                self.app.proofreader.freeze.assert_called_once()
                self.app.native.paste.assert_not_called()
                self.assertEqual(self.app.cancel_recording.call_count, int(phase == "recording"))

    def test_optional_profiles_do_not_probe_site_when_unconfigured_or_disabled(self):
        target = SimpleNamespace(app="chrome.exe")
        self.app.native.last_capture_checks = {}
        self.assertEqual(self.app.input_profile_context(target), ("", ()))
        self.app.native.site_host.assert_not_called()
        self.app.settings["input_context_profiles"] = [{"kind": "website", "identifier": "chatgpt.com", "terms": ["信楽"]}]
        self.app.main.context_enabled.setChecked(False)
        self.assertEqual(self.app.input_profile_context(target), ("", ()))
        self.app.native.site_host.assert_not_called()

    def test_profile_context_only_uses_matching_verified_target(self):
        self.app.native.last_capture_checks = {}
        self.app.native.site_host.return_value = "chatgpt.com"
        self.app.settings["input_context_profiles"] = [{"kind": "website", "identifier": "chatgpt.com", "terms": ["信楽"]}]
        self.assertEqual(self.app.input_profile_context(SimpleNamespace(app="chrome.exe"))[1], ("信楽",))
        self.assertEqual(self.app.input_profile_context(None), ("", ()))
        self.assertEqual(self.app.input_profile_context(SimpleNamespace(app="chrome.exe"), "シークレット"), ("", ()))
        self.app.native.last_capture_checks = {"password": True}
        self.assertEqual(self.app.input_profile_context(SimpleNamespace(app="chrome.exe")), ("", ()))

    def test_failed_profile_save_keeps_settings_and_editor_values(self):
        from shigaraki_win.context_profile_ui import ContextProfilesDialog
        dialog = ContextProfilesDialog([], self.app.save_context_profiles, app="chrome.exe", parent=self.app.main)
        self.addCleanup(dialog.deleteLater)
        dialog.topic.setPlainText("大学で使う用語")
        dialog.terms.setPlainText("桜美林大学")
        before = dict(self.app.settings)
        with patch.object(self.app, "write_json", side_effect=PermissionError("fixture")):
            dialog.save_entry()
        self.assertEqual(self.app.settings, before)
        self.assertEqual(dialog.topic.toPlainText(), "大学で使う用語")
        self.assertIn("保存できませんでした", dialog.message.text())

    def test_profile_editor_saves_updates_and_deletes_only_its_setting(self):
        from shigaraki_win.context_profile_ui import ContextProfilesDialog
        dialog = ContextProfilesDialog([], self.app.save_context_profiles, app="chrome.exe", host="chatgpt.com", parent=self.app.main)
        self.addCleanup(dialog.deleteLater)
        dialog.kind.setCurrentIndex(1)
        self.assertEqual(dialog.identifier.text(), "chatgpt.com")
        dialog.terms.setPlainText("信楽\n\nHaas")
        dialog.save_entry()
        saved = json.loads(self.app.settings_path.read_text())
        self.assertEqual(saved["input_context_profiles"][0]["terms"], ["信楽", "Haas"])
        self.assertEqual(saved["modifier"], 0)
        dialog.topic.setPlainText("音声入力の話題")
        dialog.save_entry()
        self.assertEqual(len(dialog.profiles), 1)
        dialog.delete_entry()
        self.assertEqual(self.app.settings["input_context_profiles"], [])

    def test_opening_profile_editor_does_not_probe_website(self):
        self.app.last_target = SimpleNamespace(app="chrome.exe")
        self.app.native.site_host.return_value = "chatgpt.com"
        with patch("shigaraki_win.context_profile_ui.ContextProfilesDialog") as editor:
            self.app.open_context_profiles()
        lookup = editor.call_args.kwargs["lookup"]
        self.app.native.site_host.assert_not_called()
        self.assertEqual(lookup("application"), ("chrome.exe", ""))
        self.app.native.site_host.assert_not_called()
        self.assertEqual(lookup("website"), ("chrome.exe", "chatgpt.com"))
        self.app.native.site_host.assert_called_once_with(self.app.last_target)

    def test_current_site_is_requested_only_on_explicit_button(self):
        from shigaraki_win.context_profile_ui import ContextProfilesDialog
        lookup = MagicMock(return_value=("chrome.exe", "chatgpt.com"))
        dialog = ContextProfilesDialog([], self.app.save_context_profiles, lookup=lookup, parent=self.app.main)
        self.addCleanup(dialog.deleteLater)
        dialog.kind.setCurrentIndex(1)
        lookup.assert_not_called()
        dialog.use_current_button.click()
        self.assertEqual(dialog.identifier.text(), "chatgpt.com")
        lookup.assert_called_once_with("website")

    def test_profile_hint_reaches_recording_session_without_changing_dictionary_rules(self):
        from shigaraki_win.native import Target
        from shigaraki_win.haas_worker import keywords_from_rules
        self.app.native.last_capture_checks = {}
        self.app.native.capture.return_value = Target(100, 200, object(), (4, 2), "", None, "", "", "chrome.exe")
        self.app.native.foreground.return_value = (100, 200)
        self.app.native.window_title.return_value = "ChatGPT"
        self.app.native.site_host.return_value = "chatgpt.com"
        self.app.settings["input_context_profiles"] = [{"kind": "website", "identifier": "chatgpt.com", "terms": ["信楽"]}]
        self.app.engine = MagicMock(ready=True)
        model = self.root / "ready.gguf"
        model.write_bytes(b"not loaded: metadata fixture")
        original_rules = self.app.word_rules()
        with patch("shigaraki_win.app.default_model", return_value=model), patch("shigaraki_win.app.Recorder"), patch("shigaraki_win.app.CaptionSession") as session:
            self.app.start(False)
        rules = self.app.engine.set_vocabulary.call_args.args[0]
        phrases = session.call_args.kwargs["phrases"]
        self.assertEqual(keywords_from_rules(rules, phrases).splitlines()[0], "信楽")
        self.assertEqual(self.app.word_rules(), original_rules)
        session.return_value.start.assert_called_once()

    def test_duplicate_target_cannot_overwrite_another_profile(self):
        from shigaraki_win.context_profile_ui import ContextProfilesDialog
        values = [{"kind": "application", "identifier": "chrome.exe", "topic": "A", "terms": ["大学"]},
                  {"kind": "application", "identifier": "chatgpt.exe", "topic": "B", "terms": ["信楽"]}]
        save = MagicMock(return_value=True)
        dialog = ContextProfilesDialog(values, save, parent=self.app.main)
        self.addCleanup(dialog.deleteLater)
        before = list(dialog.profiles)
        dialog.entries.setCurrentRow(0)
        dialog.identifier.setText("chatgpt.exe")
        dialog.save_entry()
        self.assertEqual(dialog.profiles, before)
        self.assertEqual(dialog.topic.toPlainText(), "A")
        save.assert_not_called()
        self.assertIn("登録済み", dialog.message.text())
        dialog.new_entry()
        dialog.identifier.setText("chatgpt.exe")
        dialog.save_entry()
        self.assertEqual(dialog.profiles, before)
        save.assert_not_called()

    def test_modal_editor_blocks_ctrl_recording_and_restores_it_when_closed(self):
        from PySide6.QtWidgets import QDialog
        self.app.setup.hide()
        self.app.setup.ready = True
        self.app.main.show()
        dialog = QDialog(self.app.main)
        self.addCleanup(dialog.deleteLater)
        dialog.setModal(True)
        dialog.show()
        self.qt.processEvents()
        self.assertIs(self.qt.activeModalWidget(), dialog)
        self.app.toggle_recording = MagicMock()
        self.app.commit_window = MagicMock()
        keys = set()
        self.app.native.key_down.side_effect = lambda key: key in keys
        def tick(timestamp, down):
            keys.add(0xA2) if down else keys.discard(0xA2)
            with patch("shigaraki_win.app.time.monotonic", return_value=timestamp):
                self.app.tick()
        for timestamp, down in [(100, True), (100.06, False), (100.5, False)]:
            tick(timestamp, down)
        self.app.toggle_recording.assert_not_called()
        self.app.commit_window.assert_not_called()
        dialog.hide()
        for timestamp, down in [(101, True), (101.06, False), (101.5, False)]:
            tick(timestamp, down)
        self.app.toggle_recording.assert_called_once()


if __name__ == "__main__":
    unittest.main()

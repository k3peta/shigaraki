import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shigaraki_win.settings_migration import migrate_control_default


class ControlMigrationTests(unittest.TestCase):
    def test_a1_alt_setting_moves_to_control_once_and_keeps_other_preferences(self):
        settings = {"modifier": 1, "log": False, "words": [["よみ", "表記"]]}
        self.assertTrue(migrate_control_default(settings))
        self.assertEqual(settings["modifier"], 0)
        self.assertFalse(settings["log"])
        self.assertEqual(settings["words"], [["よみ", "表記"]])
        settings["modifier"] = 1
        self.assertFalse(migrate_control_default(settings))
        self.assertEqual(settings["modifier"], 1)

    def test_new_profile_defaults_to_control(self):
        settings = {}
        self.assertTrue(migrate_control_default(settings))
        self.assertEqual(settings["modifier"], 0)
        self.assertTrue(settings["control_default_v2"])


class PersistedControlMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["Haas Ctrl migration"])

    def test_a1_migration_is_saved_and_later_user_choice_survives_restart(self):
        from shigaraki_win.app import App
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "settings.json"
            settings.write_text(json.dumps({"modifier": 1, "log": False, "words": []}))
            with patch("shigaraki_win.app.data_dir", return_value=root), \
                    patch("shigaraki_win.app.default_model", return_value=root / "missing.gguf"), \
                    patch("shigaraki_win.app.WindowsInput"), patch("shigaraki_win.app.QSystemTrayIcon"), \
                    patch("sounddevice.query_devices", return_value=[]):
                first = App(self.qt); first.timer.stop()
                try:
                    self.assertEqual(first.main.modifier.currentIndex(), 0)
                    first.main.modifier.setCurrentIndex(1)
                    self.assertEqual(json.loads(settings.read_text())["modifier"], 1)
                finally:
                    first.main.hide(); first.setup.hide(); first.panel.hide()
                second = App(self.qt); second.timer.stop()
                try:
                    self.assertEqual(second.main.modifier.currentIndex(), 1)
                    self.assertFalse(second.main.log_enabled.isChecked())
                finally:
                    second.main.hide(); second.setup.hide(); second.panel.hide()


if __name__ == "__main__":
    unittest.main()

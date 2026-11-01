import os
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from shigaraki_win.app import App
from shigaraki_win.ui import MainWindow, SetupWindow


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication(["Haas recovery tests"])

    def test_finishing_keeps_cancel_and_copy_available_but_prevents_duplicate_paste(self):
        window = MainWindow()
        try:
            window.editor.setPlainText("すでに認識した文章")
            window.busy(True, finishing=True)
            self.assertFalse(window.cancel.isHidden())
            self.assertTrue(window.copy_button.isEnabled())
            self.assertTrue(window.raw_button.isEnabled())
            self.assertFalse(window.paste_button.isEnabled())
            self.assertFalse(window.record.isEnabled())
        finally:
            window.close()

    def test_cancel_while_finishing_cancels_session_without_stopping_mic_twice(self):
        app = App.__new__(App)
        app.phase = "finishing"; app.cancelled = False; app.proof_wait_until = 42
        app.context_memory = MagicMock(); app.session = MagicMock(); app.stop = MagicMock()
        app.cancel_recording()
        self.assertTrue(app.cancelled)
        self.assertIsNone(app.proof_wait_until)
        app.session.cancel.assert_called_once(); app.stop.assert_not_called()

    def test_crashed_resident_worker_returns_to_prepare_instead_of_ready_loop(self):
        app = App.__new__(App)
        app.phase = "idle"; app.engine = SimpleNamespace(ready=False); app.setup = SetupWindow()
        app.setup.ready = True; begin, complete = MagicMock(), MagicMock()
        app.setup.begin.connect(begin); app.setup.complete.connect(complete)
        try:
            with patch("shigaraki_win.app.default_model") as model:
                model.return_value.exists.return_value = True
                app.start(False)
            self.assertFalse(app.setup.ready)
            app.setup.start_button.click()
            begin.assert_called_once(); complete.assert_not_called()
        finally:
            app.setup.close()

    def test_copy_actions_during_finishing_actually_write_recognized_text(self):
        app = App.__new__(App); app.phase = "finishing"; app.qt = MagicMock(); app.main = MagicMock()
        app.main.editor.toPlainText.return_value = "認識した文"; app.raw_text = "認識原文"
        app.copy_last(); app.qt.clipboard.return_value.setText.assert_called_with("認識した文")
        app.copy_raw(); app.qt.clipboard.return_value.setText.assert_called_with("認識原文")


if __name__ == "__main__":unittest.main()

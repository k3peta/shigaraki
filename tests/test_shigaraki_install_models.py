import io
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from shigaraki_win.install_portable import install, verify_installed_models
from shigaraki_win.setup_models import prepare_both, verify_both


class HaasAndPunctuationSetupTests(unittest.TestCase):
    def test_initial_probe_checks_haas_and_bundled_punctuation_without_qwen(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.CpuEngine") as asr, \
                patch("shigaraki_win.setup_models.PunctuationRuntime") as punctuation, \
                patch("shigaraki_win.proofread_runtime.ProofreadRuntime") as optional_proof:
            asr.return_value.ready = True
            punctuation.return_value.ready = True
            verify_both(stages.append, allow_policy_block=True, require_punctuation=True)
            self.assertEqual(stages, ["check_asr", "check_punctuation", "models_ready"])
            optional_proof.assert_not_called()
            asr.return_value.close.assert_called_once()
            punctuation.return_value.close.assert_called_once()

    def test_legacy_policy_flag_cannot_hide_a_failed_haas_runtime(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.CpuEngine") as asr:
            asr.return_value.load.side_effect = RuntimeError("invalid Haas runtime")
            with self.assertRaises(RuntimeError):
                verify_both(stages.append, allow_policy_block=True)
            asr.return_value.close.assert_called_once()
        self.assertEqual(stages, ["check_asr"])

    def test_optional_qwen_failure_and_retry_preserve_the_prepared_haas_model(self):
        from shigaraki_win import engine, proofread_runtime
        asr_data, proof_data = b"asr model fixture", b"proof model fixture"
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn.example.test/model"
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"LOCALAPPDATA": directory}), patch.object(engine, "MODEL_BYTES", len(asr_data)), patch.object(engine, "MODEL_SHA256", hashlib.sha256(asr_data).hexdigest()), patch.object(proofread_runtime, "MODEL_BYTES", len(proof_data)), patch.object(proofread_runtime, "MODEL_SHA256", hashlib.sha256(proof_data).hexdigest()):
                with patch("urllib.request.urlopen", return_value=Response(asr_data)) as initial_download:
                    prepare_both(lambda *_: None, threading.Event())
                initial_download.assert_called_once_with(engine.MODEL_URL, timeout=15)
                first = engine.default_model()
                self.assertEqual(first.read_bytes(), asr_data)
                modified = first.stat().st_mtime_ns
                # Optional Qwen is a distinct user opt-in, outside initial setup.
                with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                    with self.assertRaises(OSError):
                        proofread_runtime.prepare_model(lambda *_: None)
                self.assertEqual(first.read_bytes(), asr_data)
                with patch("urllib.request.urlopen", return_value=Response(proof_data)) as optional_download:
                    prepare_both(lambda *_: None, threading.Event())
                    proofread_runtime.prepare_model(lambda *_: None)
                optional_download.assert_called_once_with(proofread_runtime.MODEL_URL, timeout=15)
                self.assertEqual(first.stat().st_mtime_ns, modified)
                self.assertEqual(proofread_runtime.default_model().read_bytes(), proof_data)
                self.assertEqual(list(first.parent.glob("*.part")), [])

    def test_initial_download_does_not_prepare_optional_qwen(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.prepare_model") as asr, \
                patch("shigaraki_win.proofread_runtime.prepare_model") as optional_proof:
            prepare_both(lambda stage, done, total, name: stages.append((stage, done, total)), threading.Event())
        asr.assert_called_once()
        optional_proof.assert_not_called()
        self.assertTrue(all(stage == "asr_model" for stage, _, _ in stages))
        self.assertEqual(stages[-1][1], stages[-1][2])

    def test_failed_haas_download_is_not_reported_complete(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.prepare_model", side_effect=OSError("fixture offline")), \
                patch("shigaraki_win.proofread_runtime.prepare_model") as optional_proof:
            with self.assertRaises(OSError):
                prepare_both(lambda stage, done, total, name: stages.append((stage, done, total)), threading.Event())
        self.assertEqual(stages[-1][0:2], ("asr_model", 0))
        optional_proof.assert_not_called()

    def test_cancellation_before_download_never_starts_a_model_fetch(self):
        cancel = threading.Event(); cancel.set()
        with patch("shigaraki_win.setup_models.engine.prepare_model") as asr:
            with self.assertRaises(InterruptedError):
                prepare_both(lambda *_: None, cancel)
        asr.assert_not_called()

    def test_cancel_after_haas_download_retains_the_obtained_model(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            obtained = Path(directory) / "haas.gguf"
            def download(*args, **kwargs):
                obtained.write_bytes(b"verified Haas fixture")
                cancel.set()
            stages = []
            with patch("shigaraki_win.setup_models.engine.prepare_model", side_effect=download), \
                    patch("shigaraki_win.proofread_runtime.prepare_model") as optional_proof:
                with self.assertRaises(InterruptedError):
                    prepare_both(lambda stage, done, total, name: stages.append((stage, done, total)), cancel)
            self.assertEqual(obtained.read_bytes(), b"verified Haas fixture")
            self.assertEqual(stages[-1][1], 0)
            optional_proof.assert_not_called()

    def test_punctuation_failure_closes_both_resident_models_without_ready_signal(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.CpuEngine") as asr, \
                patch("shigaraki_win.setup_models.PunctuationRuntime") as punctuation:
            asr.return_value.ready = True
            punctuation.return_value.load.side_effect = RuntimeError("fixture failed")
            with self.assertRaises(RuntimeError):
                verify_both(stages.append, require_punctuation=True)
            asr.return_value.load.assert_called_once()
            asr.return_value.close.assert_called_once()
            punctuation.return_value.close.assert_called_once()
        self.assertEqual(stages, ["check_asr", "check_punctuation"])

    def test_ready_signal_requires_actual_haas_runtime_readiness(self):
        stages = []
        with patch("shigaraki_win.setup_models.engine.CpuEngine") as asr:
            asr.return_value.ready = False
            with self.assertRaises(RuntimeError):
                verify_both(stages.append)
        self.assertNotIn("models_ready", stages)


class InstallerModelFlowTests(unittest.TestCase):
    def test_initial_probe_accepts_ready_only_after_haas_and_punctuation_check(self):
        process = MagicMock(); process.stdout = io.BytesIO(b'{"stage":"check_asr"}\n{"stage":"check_punctuation"}\n{"stage":"models_ready"}\n')
        process.poll.return_value = 0; process.returncode = 0
        stages = []
        with patch("shigaraki_win.install_portable.subprocess.Popen", return_value=process) as launch:
            verify_installed_models(Path("fixture"), lambda stage, *_: stages.append(stage), threading.Event())
        self.assertEqual(stages, ["check_asr", "check_punctuation"])
        self.assertIn("--verify-punctuation", launch.call_args.args[0])
    def test_launch_happens_after_haas_download_and_punctuation_probe(self):
        order = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("shigaraki_win.install_portable.copy_payload", side_effect=lambda *a: order.append("copy")), patch("shigaraki_win.setup_models.prepare_both", side_effect=lambda *a: order.append("models")), patch("shigaraki_win.install_portable.verify_installed_models", side_effect=lambda *a: order.append("checked")), patch("shigaraki_win.install_portable.subprocess.run", side_effect=lambda *a, **kw: order.append("shortcut")), patch("shigaraki_win.install_portable.subprocess.Popen", side_effect=lambda *a, **kw: order.append("launch")) as launch:
                self.assertTrue(install(root, root/"installed", lambda *_:None, threading.Event(), root/"log"))
            self.assertEqual(order, ["copy", "models", "checked", "shortcut", "launch"])
            self.assertIn("--installed", launch.call_args.args[0])
            self.assertNotIn("--setup", launch.call_args.args[0])

    def test_model_failure_does_not_create_shortcut_or_launch_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.setup_models.prepare_both", side_effect=OSError("offline")), patch("shigaraki_win.install_portable.verify_installed_models") as verify, patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                with self.assertRaises(OSError):
                    install(root, root/"installed", lambda *_:None, threading.Event(), root/"log")
            verify.assert_not_called(); launch.assert_not_called()

    def test_probe_failure_does_not_launch_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.setup_models.prepare_both"), patch("shigaraki_win.install_portable.verify_installed_models", side_effect=RuntimeError("not ready")), patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                with self.assertRaises(RuntimeError):
                    install(root, root/"installed", lambda *_:None, threading.Event(), root/"log")
            launch.assert_not_called()

    def test_clean_exit_without_ready_message_is_a_failed_probe(self):
        process = MagicMock(); process.stdout = io.BytesIO(b'{"stage":"check_asr"}\n')
        process.poll.return_value = 0; process.returncode = 0
        with patch("shigaraki_win.install_portable.subprocess.Popen", return_value=process):
            with self.assertRaises(RuntimeError):
                verify_installed_models(Path("fixture"), lambda *_:None, threading.Event())

    def test_probe_timeout_terminates_the_owned_process(self):
        process = MagicMock(); process.stdout = io.BytesIO(); process.poll.return_value = None
        with patch("shigaraki_win.install_portable.subprocess.Popen", return_value=process):
            with self.assertRaises(TimeoutError):
                verify_installed_models(Path("fixture"), lambda *_:None, threading.Event(), timeout=0)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_probe_cancellation_terminates_the_owned_process(self):
        process = MagicMock(); process.stdout = io.BytesIO(); process.poll.return_value = None
        cancel = threading.Event(); cancel.set()
        with patch("shigaraki_win.install_portable.subprocess.Popen", return_value=process):
            with self.assertRaises(InterruptedError):
                verify_installed_models(Path("fixture"), lambda *_:None, cancel)
        process.terminate.assert_called_once()


class InstallationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["Installer fixture"])

    def test_download_stage_and_completion_are_distinct_and_failures_can_retry(self):
        from shigaraki_win.ui import InstallWindow, COPY
        w = InstallWindow()
        w.update_stage("proof_model", 20_000_000, 40_000_000, 3)
        self.assertFalse(w.install_finished)
        self.assertEqual(w.progress.value(), 50)
        self.assertEqual(w.status.text(), COPY["install_proof_model"])
        w.update_stage("failed", 0, 0, 5, finished=True)
        self.assertFalse(w.retry_button.isHidden())
        self.assertEqual(w.progress.value(), 0)
        w.update_stage("complete", 0, 0, 7, finished=True)
        self.assertTrue(w.retry_button.isHidden())
        self.assertEqual(w.progress.value(), 100)
        w.hide()

    def test_failure_identifies_model_loading_instead_of_suggesting_download_failure(self):
        from shigaraki_win.ui import InstallWindow, COPY
        w = InstallWindow()
        try:
            w.update_stage("failed", 0, 0, 17, finished=True, failed_stage="check_proof")
            self.assertIn(COPY["install_check_proof"], w.status.text())
            self.assertIn("model-check-error.txt", w.status.text())
            self.assertFalse(w.retry_button.isHidden())
        finally:
            w.hide()


if __name__ == "__main__":
    unittest.main()

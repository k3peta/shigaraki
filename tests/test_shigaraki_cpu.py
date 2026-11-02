from __future__ import annotations

import hashlib
import contextlib
import ctypes as C
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from shigaraki_win import engine
from shigaraki_win.gestures import ControlGesture
from shigaraki_win.native import may_restore
from shigaraki_win.session import CaptionSession, replace_words, format_text


class GestureTests(unittest.TestCase):
    def test_spoken_punctuation_commands_do_not_rewrite_normal_words(self):
        self.assertEqual(format_text("今日は晴れです てん 明日は雨です まる", []), "今日は晴れです、明日は雨です。")
        self.assertEqual(format_text("今日は晴れです。点。明日は雨です。丸。", []), "今日は晴れです、明日は雨です。")
        self.assertEqual(format_text("今日は晴れですまる", []), "今日は晴れです。")
        self.assertEqual(format_text("今日は晴れですてん明日は雨ですまる", []), "今日は晴れです、明日は雨です。")
        for text in ("丸い形です", "収まる", "てんとう虫", "点数は100点です", "まるで夢のようです"):
            self.assertEqual(format_text(text, []), text)
        self.assertEqual(format_text("まる", [["まる", "丸"]]), "丸")
    def test_double_tap_and_hold_are_exclusive(self):
        g = ControlGesture()
        def update(t, down, isolated=True, busy=False, allow=True):
            return g.update(t, down, isolated, busy=busy, allow_hold=allow)
        self.assertEqual(update(0, True), [])
        self.assertEqual(update(.08, False), [])
        self.assertEqual(update(.2, True), [])
        self.assertEqual(update(.28, False), ["double_tap"])
        self.assertEqual(update(1, True), [])
        self.assertEqual(update(1.25, True), ["hold_start"])
        self.assertEqual(update(1.3, False, busy=True), [])
        self.assertEqual(update(1.39, False, busy=True), ["hold_end"])

    def test_control_shortcut_typing_never_starts_recording(self):
        g = ControlGesture()
        g.update(0, True, True, busy=False, allow_hold=True)
        self.assertEqual(g.update(.1, True, False, busy=False, allow_hold=True), [])
        self.assertEqual(g.update(1, True, True, busy=False, allow_hold=True), [])
        self.assertEqual(g.update(1.1, False, True, busy=False, allow_hold=True), [])

    def test_false_release_is_ignored_while_recording(self):
        g = ControlGesture()
        g.update(0, True, True, busy=False, allow_hold=True)
        g.update(.3, True, True, busy=False, allow_hold=True)
        self.assertEqual(g.update(.5, False, True, busy=True, allow_hold=False), [])
        self.assertEqual(g.update(.52, True, False, busy=True, allow_hold=False), [])
        self.assertTrue(g.holding)
        self.assertEqual(g.update(1, False, True, busy=True, allow_hold=False), [])
        self.assertEqual(g.update(1.1, False, True, busy=True, allow_hold=False), ["hold_end"])

    def test_finishing_ignores_hold_and_does_not_start_when_finishing_ends(self):
        g = ControlGesture()
        self.assertEqual(g.update(0, True, True, busy=True, allow_hold=False), [])
        self.assertEqual(g.update(.8, True, True, busy=False, allow_hold=True), [])

    def test_window_taps_can_work_when_alt_is_recording_modifier(self):
        g = ControlGesture()
        for t, down in [(0, True), (.08, False), (.2, True)]:
            g.update(t, down, True, busy=False, allow_hold=False)
        self.assertEqual(g.update(.28, False, True, busy=False, allow_hold=False), ["double_tap"])


class EngineTests(unittest.TestCase):
    def test_haas_worker_is_resident_and_cpu_process_is_closed_once(self):
        worker = MagicMock()
        worker.ready = True
        with patch("shigaraki_win.engine.valid_model", return_value=True), \
                patch("shigaraki_win.rust_worker.RustWorker", return_value=worker) as factory:
            model = engine.CpuEngine(Path("fixture.gguf"), executable=Path("speech.exe"))
            model.load()
            model.load()
            factory.assert_called_once_with(Path("speech.exe"), Path("fixture.gguf"), model.threads, None)
            worker.start.assert_called_once_with(None)
            # Rust completes model warmup before its ready event. Python only
            # leases the resident process for a recording, without reloading it.
            self.assertTrue(model.ready)
            with model.session_worker() as recording:
                self.assertIs(recording, worker)
            worker.start.assert_called_once_with(None)
            model.close()
            model.close()
            worker.close.assert_called_once()
            self.assertFalse(model.ready)

    def test_cancelled_preparation_terminates_the_owned_worker(self):
        cancel = threading.Event()
        worker = MagicMock()
        def cancel_during_start(*args):
            cancel.set()
            raise InterruptedError("startup cancelled")
        worker.start.side_effect = cancel_during_start
        with patch("shigaraki_win.engine.valid_model", return_value=True), \
                patch("shigaraki_win.rust_worker.RustWorker", return_value=worker):
            model = engine.CpuEngine(Path("fixture.gguf"), executable=Path("fixture-speech.exe"))
            with self.assertRaises(InterruptedError):
                model.load(cancel=cancel)
            worker.close.assert_called_once_with(kill=True)
            worker.transcribe.assert_not_called()
            self.assertFalse(model.ready)

    def test_pre_cancelled_preparation_never_creates_a_worker(self):
        cancel = threading.Event(); cancel.set()
        with patch("shigaraki_win.rust_worker.RustWorker") as factory:
            model = engine.CpuEngine(Path("missing.gguf"))
            with self.assertRaises(InterruptedError):
                model.load(cancel=cancel)
            factory.assert_not_called()
            self.assertFalse(model.ready)

    def test_silence_and_cancel_do_not_load_model(self):
        model = engine.CpuEngine(Path("missing.bin"))
        cancel = threading.Event()
        cancel.set()
        with patch.object(model, "load") as load:
            self.assertEqual(model.transcribe(np.zeros(16000, dtype=np.float32)), "")
            self.assertEqual(model.transcribe(np.ones(16000), cancel=cancel), "")
        load.assert_not_called()
        self.assertFalse(model.ready)

    def test_invalid_audio_rejected(self):
        with self.assertRaises(ValueError):
            engine.CpuEngine(Path("unused")).transcribe(np.array([np.nan]))

    def test_missing_or_damaged_model_rejected_before_binding(self):
        with self.assertRaises(ValueError):
            engine.CpuEngine(Path("missing.bin")).load()

    def test_download_hash_and_existing_file_are_protected(self):
        content = b"model fixture"
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn-lfs.huggingface.co/fixture"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            with patch.object(engine, "MODEL_BYTES", len(content)), patch.object(engine, "MODEL_SHA256", hashlib.sha256(content).hexdigest()):
                with patch.object(engine.urllib.request, "urlopen", return_value=Response(content)):
                    engine.prepare_model(path)
                self.assertTrue(engine.valid_model(path))
                path.write_bytes(b"user file")
                with self.assertRaises(ValueError):
                    engine.prepare_model(path)
                self.assertEqual(path.read_bytes(), b"user file")

    def test_bad_download_is_not_installed(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn-lfs.huggingface.co/fixture"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            with patch.object(engine, "MODEL_BYTES", 4), patch.object(engine, "MODEL_SHA256", "0" * 64), patch.object(engine.urllib.request, "urlopen", return_value=Response(b"bad!")):
                with self.assertRaises(ValueError):
                    engine.prepare_model(path)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_owned_corrupt_cache_is_preserved_and_repaired(self):
        content = b"model fixture"
        class Response(io.BytesIO):
            def geturl(self):
                return "https://cdn-lfs.huggingface.co/fixture"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.bin"
            path.write_bytes(b"damaged cache")
            with patch.object(engine, "default_model", return_value=path), patch.object(engine, "MODEL_BYTES", len(content)), patch.object(engine, "MODEL_SHA256", hashlib.sha256(content).hexdigest()), patch.object(engine.urllib.request, "urlopen", return_value=Response(content)):
                engine.prepare_model(path, repair=True)
                self.assertTrue(engine.valid_model(path))
            self.assertEqual(next(Path(directory).glob("*.invalid-*")).read_bytes(), b"damaged cache")


class SessionTests(unittest.TestCase):
    def test_streaming_recorder_stops_without_creating_wav(self):
        from windows_voice_typer.recorder import Recorder
        recorder = Recorder()
        stream = MagicMock()
        recorder._stream = stream
        recorder._chunks = [np.ones((800, 1), dtype=np.float32)]
        with patch.object(recorder, "audio_to_wav") as write_wav:
            audio = recorder.stop_audio()
            self.assertEqual(len(audio), 800)
            self.assertFalse(recorder.is_recording)
            stream.stop.assert_called_once()
            stream.close.assert_called_once()
            write_wav.assert_not_called()

    def test_every_sample_including_short_tail_is_consumed_once(self):
        audio = np.linspace(.01, .3, 65000, dtype=np.float32)
        chunks = []
        stream = MagicMock()
        stream.push.side_effect = lambda value: chunks.append(value.copy())
        stream.results.return_value = []
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: audio), lambda _: None)
        session.stop()
        session.start()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(session.error, "")
        np.testing.assert_array_equal(np.concatenate(chunks), audio)
        self.assertEqual(session.last_samples, len(audio))
        self.assertEqual(len(chunks[-1]), 65000 % 5120)
        stream.finish.assert_called_once()
        stream.close.assert_called_once()

    def test_provisional_and_final_results_replace_without_duplication(self):
        stream = MagicMock()
        stream.results.side_effect = [[("途中", False, .32)], [("途中の本文", False, .64)], [("途中の本文。", True, .64)]]
        values = []
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.ones(10240, dtype=np.float32)*.1), values.append)
        session.stop(); session.start()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(values, ["途中", "途中の本文", "途中の本文。"])
        self.assertEqual(session.text, "途中の本文。")

    def test_native_utterance_final_does_not_stop_recording(self):
        stream = MagicMock()
        stream.results.side_effect = [[("最初の文。", True, .32)], [("次の文", False, .64)], [("次の文。", True, .64)]]
        values = []
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.ones(10240, dtype=np.float32)*.1), values.append)
        session.start()
        deadline = time.monotonic()+1
        while len(values) < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(session.done.is_set())
        self.assertEqual(values[-1], "最初の文。次の文")
        session.stop()
        self.assertTrue(session.done.wait(1))
        self.assertEqual(session.text, "最初の文。次の文。")

    def test_long_quiet_pause_rotates_acoustic_state_without_ending_session(self):
        streams = [MagicMock(), MagicMock()]
        streams[0].results.return_value = [("前。", False, .32)]
        streams[1].results.return_value = [("後。", False, .32)]
        chunks = []
        for stream in streams:
            stream.push.side_effect = lambda audio: chunks.append(audio.copy())
        audio = np.concatenate([np.ones(5120,dtype=np.float32)*.1,
                                np.zeros(20480,dtype=np.float32),np.ones(5120,dtype=np.float32)*.2])
        engine = MagicMock();engine.stream.side_effect = [contextlib.closing(s) for s in streams]
        phrases = ("信楽", "桜美林大学")
        session = CaptionSession(engine, SimpleNamespace(snapshot_audio=lambda: audio), lambda _:None,
                                 phrases=phrases)
        session.stop();session.start()
        self.assertTrue(session.done.wait(1))
        self.assertEqual(session.error, "")
        self.assertEqual(session.text, "前。後。")
        self.assertEqual(engine.stream.call_count, 2)
        self.assertEqual([call.kwargs for call in engine.stream.call_args_list],
                         [{"phrases": phrases}, {"phrases": phrases}])
        np.testing.assert_array_equal(np.concatenate(chunks), audio)

    def test_cancelled_decode_cannot_publish_stale_text(self):
        entered, release = threading.Event(), threading.Event()
        stream = MagicMock()
        stream.push.side_effect = lambda _: (entered.set(), release.wait(2))
        stream.results.return_value = [("古い本文", False, .32)]
        values = []
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.ones(5120, dtype=np.float32)*.1), values.append)
        session.start()
        self.assertTrue(entered.wait(2));session.cancel();release.set()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(values, [])
        stream.finish.assert_not_called()
        stream.close.assert_called_once()

    def test_silence_does_not_decode_or_emit_words(self):
        stream = MagicMock()
        values = []
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.zeros(50000, dtype=np.float32)), values.append)
        session.stop();session.start()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(session.error, "")
        stream.push.assert_not_called()
        stream.finish.assert_not_called()
        self.assertEqual(values, [])

    def test_dictionary_replacements_do_not_cascade(self):
        self.assertEqual(replace_words("しがらき", [["しがらき", "信楽"], ["信楽", "他の語"]]), "信楽")

    def test_restore_never_redirects_to_another_application(self):
        self.assertTrue(may_restore(10, 20, 10, 20, 30))
        self.assertTrue(may_restore(10, 20, 40, 30, 30))
        self.assertFalse(may_restore(10, 20, 50, 60, 30))
        self.assertFalse(may_restore(0, 20, 40, 30, 30))


class MicrophoneTests(unittest.TestCase):
    def test_default_input_output_pair_is_indexed_without_list_type_assumption(self):
        from windows_voice_typer.recorder import _candidate_input_devices
        class Pair:
            def __getitem__(self, index):
                return (2, 7)[index]
        sd = SimpleNamespace(default=SimpleNamespace(device=Pair()), query_devices=lambda: [
            {"max_input_channels": 0}, {"max_input_channels": 1}, {"max_input_channels": 1}])
        self.assertEqual(_candidate_input_devices(sd, "auto"), [2, 1])
        self.assertEqual(_candidate_input_devices(sd, 1), [1])

    def test_stream_that_fails_to_start_is_closed_before_trying_next_input(self):
        from windows_voice_typer.recorder import Recorder
        failed, working = MagicMock(), MagicMock()
        failed.start.side_effect = RuntimeError("device busy")
        sd = SimpleNamespace(default=SimpleNamespace(device=[0, 1]),
                             query_devices=lambda index=None: ([{"max_input_channels": 1}, {"max_input_channels": 1}]
                                 if index is None else {"name": "fixture"}), InputStream=MagicMock(side_effect=[failed, working]))
        recorder = Recorder()
        with patch.dict("sys.modules", sounddevice=sd):
            recorder.start()
        failed.close.assert_called_once()
        self.assertIs(recorder._stream, working)
        working.start.assert_called_once()

    def test_incremental_pcm_reads_cross_callback_boundaries_without_loss(self):
        from windows_voice_typer.recorder import Recorder
        recorder = Recorder()
        expected = np.arange(20, dtype=np.float32).reshape(-1, 1)
        for start, end in [(0, 3), (3, 14), (14, 20)]:
            recorder._callback(expected[start:end], end-start, None, None)
        self.assertEqual(recorder.recorded_seconds, 20/16000)
        np.testing.assert_array_equal(recorder.read_audio(2, 13), expected[2:15])
        np.testing.assert_array_equal(recorder.read_audio(15, 10), expected[15:])
        self.assertIsNone(recorder.read_audio(20, 5))

    def test_stop_failure_still_closes_device(self):
        from windows_voice_typer.recorder import Recorder
        recorder = Recorder()
        stream = MagicMock()
        stream.stop.side_effect = RuntimeError("fixture stop error")
        recorder._stream = stream
        with self.assertRaises(RuntimeError):
            recorder.stop_audio()
        stream.close.assert_called_once()
        self.assertFalse(recorder.is_recording)


class StreamingRegressionTests(unittest.TestCase):
    def test_caption_is_published_before_recording_stops(self):
        seen = threading.Event()
        stream = MagicMock()
        stream.results.return_value = [("録音中の字幕", False, .32)]
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.ones(5120, dtype=np.float32)*.1),
                                 lambda text: seen.set())
        session.start()
        try:
            self.assertTrue(seen.wait(2))
            self.assertFalse(session.done.is_set())
            self.assertFalse(session.stop_requested.is_set())
            self.assertIsNotNone(session.metrics["first_caption_seconds"])
            self.assertNotIn("録音中の字幕", json.dumps(session.metrics, ensure_ascii=False))
        finally:
            session.stop()
            self.assertTrue(session.done.wait(2))

    def test_stop_during_an_empty_read_still_drains_the_late_capture_tail(self):
        stream = MagicMock()
        stream.results.return_value = []
        tail = np.ones(80, dtype=np.float32)*.1
        calls = [0]
        def read(start, limit):
            calls[0] += 1
            if calls[0] == 1:
                session.stop()
                return None
            return tail if not start else None
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(read_audio=read, recorded_seconds=len(tail)/16000), lambda _:None)
        session.start()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(session.last_samples, len(tail))
        np.testing.assert_array_equal(stream.push.call_args.args[0], tail)
        stream.finish.assert_called_once()

    def test_non_finite_audio_is_reported_and_stream_is_closed(self):
        stream = MagicMock()
        session = CaptionSession(SimpleNamespace(stream=lambda: contextlib.closing(stream)),
                                 SimpleNamespace(snapshot_audio=lambda: np.full(5120, np.nan)), lambda _: None)
        session.stop();session.start()
        self.assertTrue(session.done.wait(2))
        self.assertEqual(session.error, "ValueError")
        stream.push.assert_not_called()
        stream.close.assert_called_once()

    def test_native_config_forces_cpu_and_preserves_user_control_of_stopping(self):
        from shigaraki_win.nemotron_native import Native, Config
        lib = MagicMock()
        def create(config, out):
            c = C.cast(config, C.POINTER(Config)).contents
            self.assertEqual(c.backend.contents.gpu, -1)
            self.assertTrue(c.endpointing.contents.enable)
            self.assertEqual(c.endpointing.contents.stop_history_eou_ms, 1000)
            self.assertEqual(c.streaming.contents.rnnt_right_context, 3)
            self.assertEqual(c.model.contents.path.decode(), "日本語モデル.gguf")
            C.cast(out, C.POINTER(C.c_void_p))[0] = 123
            return 0
        lib.nemo_speech_asr_create.side_effect = create
        with patch("shigaraki_win.nemotron_native.C.CDLL", return_value=lib):
            native = Native(Path("fixture.dll"))
            self.assertEqual(native.load(Path("日本語モデル.gguf")).value, 123)

    def test_native_result_is_freed_and_cancel_does_not_pull_more_results(self):
        from shigaraki_win.nemotron_native import Stream, Options
        native = MagicMock()
        native.recognition_options_default.return_value = Options()
        def opened(recognizer, options, out):
            self.assertEqual(C.cast(options, C.POINTER(Options)).contents.language_code, b"ja-JP")
            C.cast(out, C.POINTER(C.c_void_p))[0] = 5
            return 0
        native.streaming_recognize.side_effect = opened
        calls = [0]
        def next_result(handle, out):
            C.cast(out, C.POINTER(C.c_void_p))[0] = 7 if calls[0] == 0 else None
            calls[0] += 1
            return 0
        native.stream_next.side_effect = next_result
        native.result_transcript.return_value = "途中。".encode()
        native.result_is_final.return_value = False
        native.result_audio_processed.return_value = .32
        stream = Stream(native, 1)
        self.assertEqual(list(stream.results()), [("途中。", False, .32)])
        native.result_destroy.assert_called_once()
        cancel = threading.Event();cancel.set()
        self.assertEqual(list(stream.results(cancel)), [])
        self.assertEqual(calls[0], 2)
        stream.close();stream.close()
        native.stream_close.assert_called_once()


class UiStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication(["Shigaraki fixture"])

    def setUp(self):
        from shigaraki_win.app import App
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.patches = [patch("shigaraki_win.app.WindowsInput"),
                        patch("shigaraki_win.app.QSystemTrayIcon"),
                        patch("shigaraki_win.app.data_dir", return_value=self.root),
                        patch("shigaraki_win.app.default_model", return_value=self.root / "missing.bin"),
                        patch("sounddevice.query_devices", return_value=[])]
        for p in self.patches:
            p.start()
        self.app = App(self.qt)
        self.app.timer.stop()
        self.app.settings["setup_complete"] = True
        self.app.setup.hide()

    def make_ready_engine(self):
        self.app.engine = SimpleNamespace(ready=True, threads=6,
            set_vocabulary=MagicMock(), close=MagicMock())

    def enable_optional_proofreading(self):
        from PySide6.QtCore import QSignalBlocker
        with QSignalBlocker(self.app.main.proof_enabled):
            self.app.main.proof_enabled.setChecked(True)
        self.app.settings["proofreading"] = True

    def test_haas_defaults_select_control_and_leave_optional_qwen_off(self):
        self.assertEqual(self.app.main.modifier.currentIndex(), 0)
        self.assertFalse(self.app.main.proof_enabled.isChecked())
        self.assertFalse(self.app.proof_engine.ready)
        self.assertFalse(self.app.engine.ready)

    def tearDown(self):
        self.app.main.hide()
        self.app.panel.hide()
        self.app.setup.hide()
        for p in reversed(self.patches):
            p.stop()
        self.temporary.cleanup()

    def test_model_file_alone_does_not_start_recording_before_model_is_ready(self):
        with patch("shigaraki_win.app.default_model") as model, patch("shigaraki_win.app.Recorder") as recorder:
            model.return_value.exists.return_value = True
            self.app.start(False)
        recorder.assert_not_called()
        self.assertTrue(self.app.setup.isVisible())
        self.assertEqual(self.app.phase, "idle")

    def test_worker_caption_is_rendered_before_recording_stops(self):
        self.app.phase = "recording"
        self.app.generation = 3
        self.app.panel.show()
        worker = threading.Thread(target=lambda: self.app.signals.caption.emit(3, "録音中の字幕"))
        worker.start()
        worker.join(1)
        deadline = time.monotonic() + 2
        while self.app.main.editor.toPlainText() != "録音中の字幕" and time.monotonic() < deadline:
            self.qt.processEvents()
        self.assertEqual(self.app.phase, "recording")
        self.assertEqual(self.app.main.editor.toPlainText(), "録音中の字幕")
        self.assertEqual(self.app.panel.text.text(), "録音中の字幕")

    def test_builtin_vocabulary_is_visible_without_overwriting_user_settings(self):
        before = list(self.app.settings["words"])
        self.assertEqual(before, [])
        self.assertIn("アンチグラビティ → Antigravity", self.app.main.words.toPlainText())
        self.assertIn("コーデックス → Codex", self.app.main.words.toPlainText())
        self.assertNotIn("桜美林", self.app.main.words.toPlainText())
        self.assertNotIn("しがらき → 信楽", self.app.main.words.toPlainText())
        self.assertEqual(self.app.settings["words"], before)

    def test_recording_passes_written_terms_to_both_and_readings_only_to_asr(self):
        self.app.settings["words"] = [["しがらき", "信楽"],
            ["おうびりんだいがく", "桜美林大学"], ["めいじだいがく", "明治大学"]]
        self.make_ready_engine()
        self.app.proof_engine = SimpleNamespace(ready=True)
        self.enable_optional_proofreading()
        with patch("shigaraki_win.app.default_model") as model, \
             patch("shigaraki_win.app.Recorder"), \
             patch("shigaraki_win.app.CaptionSession") as session, \
             patch("shigaraki_win.app.SentenceProofreader") as proof:
            model.return_value.exists.return_value = True
            self.app.start(False)
        terms = session.call_args.kwargs["phrases"]
        self.assertIn("桜美林大学", terms)
        self.assertIn("明治大学", terms)
        protected = proof.call_args.kwargs["protected"]
        self.assertIn("桜美林大学", protected)
        self.assertIn("信楽", protected)
        self.assertTrue(set(protected).issubset(terms))
        for reading in ("しがらき", "シガラキ"):
            self.assertIn(reading, terms)
            self.assertNotIn(reading, protected)
        session.return_value.start.assert_called_once()

    def test_recording_starts_with_legacy_settings_without_a_words_key(self):
        self.app.settings.pop("words", None)
        self.make_ready_engine()
        self.app.proof_engine = SimpleNamespace(ready=True)
        self.enable_optional_proofreading()
        with patch("shigaraki_win.app.default_model") as model, \
             patch("shigaraki_win.app.Recorder") as recorder, \
             patch("shigaraki_win.app.CaptionSession") as session, \
             patch("shigaraki_win.app.SentenceProofreader"):
            model.return_value.exists.return_value = True
            self.app.start(False)
        recorder.return_value.start.assert_called_once()
        session.assert_called_once()
        session.return_value.start.assert_called_once()
        self.assertEqual(self.app.phase, "recording")

    def test_all_user_terms_are_protected_even_when_asr_bias_is_bounded(self):
        self.app.settings["words"] = [[f"読み{i}", f"用語{i}"] for i in range(70)]
        self.make_ready_engine()
        self.app.proof_engine = SimpleNamespace(ready=True)
        self.enable_optional_proofreading()
        with patch("shigaraki_win.app.default_model") as model, \
             patch("shigaraki_win.app.Recorder"), \
             patch("shigaraki_win.app.CaptionSession") as session, \
             patch("shigaraki_win.app.SentenceProofreader") as proof:
            model.return_value.exists.return_value = True
            self.app.start(False)
        self.assertEqual(len(session.call_args.kwargs["phrases"]), 64)
        self.assertIn("用語0", proof.call_args.kwargs["protected"])
        self.assertIn("Antigravity", proof.call_args.kwargs["protected"])

    def test_current_field_context_is_passed_without_saving_reference_text(self):
        topic = "秘密の参照文脈：早稲田大学での授業準備です。"
        target = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2),
                                 value=topic, prefix=topic, suffix="", app="editor.exe")
        self.app.native.capture.return_value = target
        self.make_ready_engine()
        self.app.proof_engine = SimpleNamespace(ready=True)
        self.enable_optional_proofreading()
        with patch("shigaraki_win.app.default_model") as model, \
             patch("shigaraki_win.app.Recorder"), patch("shigaraki_win.app.CaptionSession"), \
             patch("shigaraki_win.app.SentenceProofreader") as proof:
            model.return_value.exists.return_value = True
            self.app.start(False)
        self.assertEqual(proof.call_args.kwargs["context"], topic)
        self.assertEqual(self.app.context_source, "field")
        self.assertNotIn(topic, json.dumps(self.app.settings, ensure_ascii=False))

    def test_context_off_freezes_old_worker_and_ignores_late_revision(self):
        old = MagicMock(); self.app.proofreader = old
        self.app.phase = "recording"; self.app.generation = 7
        self.app.main.editor.setPlainText("現在の字幕")
        self.app.main.context_enabled.setChecked(False)
        self.app.proofread_caption(7)
        old.freeze.assert_called_once()
        self.assertIsNone(self.app.proofreader)
        self.assertEqual(self.app.main.editor.toPlainText(), "現在の字幕")
        self.assertFalse(json.loads(self.app.settings_path.read_text())["context"])

    def test_asr_uses_current_topic_even_without_a_ready_proofreading_model(self):
        topic = "桜美林大学の授業について。"
        target = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value=topic, prefix=topic, suffix="")
        custom = [["おうびりんだいがく", "桜美林大学"]] + [[f"読み{i}", f"用語{i}"] for i in range(80)]
        for enabled, ready, context_enabled in ((False, False, True), (True, False, True), (True, True, False)):
            with self.subTest(proof=enabled, ready=ready, context=context_enabled):
                self.app.phase = "idle"
                self.app.settings["words"] = custom
                self.app.main.proof_enabled.setChecked(enabled)
                self.app.main.context_enabled.setChecked(context_enabled)
                self.app.native.capture.return_value = target
                self.make_ready_engine()
                self.app.proof_engine = SimpleNamespace(ready=ready)
                with patch("shigaraki_win.app.default_model") as model, \
                     patch("shigaraki_win.app.Recorder"), patch("shigaraki_win.app.CaptionSession") as session, \
                     patch("shigaraki_win.app.SentenceProofreader") as proof:
                    model.return_value.exists.return_value = True
                    self.app.start(False)
                self.assertEqual("桜美林大学" in session.call_args.kwargs["phrases"], context_enabled)
                if enabled and ready:
                    self.assertEqual(proof.call_args.kwargs["context"], "")
                else:
                    proof.assert_not_called()
                self.assertNotIn(topic, json.dumps(self.app.settings, ensure_ascii=False))

    def test_recent_page_steers_asr_and_off_clears_memory(self):
        self.app.settings["words"] = [["おうびりん", "桜美林大学"]] + [[f"読み{i}", f"用語{i}"] for i in range(80)]
        self.app.page_context.observe("桜美林大学 - Chrome")
        target = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value="", prefix="", suffix="")
        self.app.native.capture.return_value = target
        self.app.native.last_capture_checks = {}
        self.app.native.window_title.return_value = "Codex"
        self.make_ready_engine()
        self.app.main.proof_enabled.setChecked(False)
        with patch("shigaraki_win.app.default_model") as model, patch("shigaraki_win.app.Recorder"), \
                patch("shigaraki_win.app.CaptionSession") as session:
            model.return_value.exists.return_value = True
            self.app.start(False)
        self.assertIn("桜美林大学", session.call_args.kwargs["phrases"])
        self.assertEqual(self.app.context_source, "field_and_pages")
        self.app.main.context_enabled.setChecked(False)
        self.assertEqual(self.app.page_context.reference(), "")

    def test_noneditable_external_window_clears_previous_destination(self):
        self.app.last_target = SimpleNamespace(hwnd=10, pid=20)
        self.app.native.capture.return_value = None
        self.app.native.foreground.return_value = (30, 40)
        self.app.show_main()
        self.assertIsNone(self.app.last_target)

    def test_password_target_drops_even_allowed_recent_pages(self):
        self.app.settings["words"] = [["おうびりん", "桜美林大学"]] + [[f"読み{i}", f"用語{i}"] for i in range(80)]
        self.app.page_context.observe("桜美林大学 - Chrome")
        self.app.native.capture.return_value = None
        self.app.native.foreground.return_value = (10, 20)
        self.app.native.last_capture_checks = {"password": True}
        self.make_ready_engine()
        self.app.main.proof_enabled.setChecked(False)
        with patch("shigaraki_win.app.default_model") as model, patch("shigaraki_win.app.Recorder"), \
                patch("shigaraki_win.app.CaptionSession") as session:
            model.return_value.exists.return_value = True
            self.app.start(False)
        self.assertNotIn("桜美林大学", session.call_args.kwargs["phrases"])
        self.assertEqual(self.app.page_context.reference(), "")
        self.app.native.window_title.assert_not_called()

    def test_microphone_failure_is_visible_and_preserves_previous_result(self):
        self.make_ready_engine()
        self.app.last_input = "前回の本文"
        with patch("shigaraki_win.app.default_model") as model, patch("shigaraki_win.app.Recorder") as recorder:
            model.return_value.exists.return_value = True
            recorder.return_value.start.side_effect = RuntimeError("fixture mic unavailable")
            self.app.start(False)
        self.assertEqual(self.app.phase, "idle")
        self.assertIsNone(self.app.recorder)
        self.assertEqual(self.app.main.editor.toPlainText(), "前回の本文")
        self.assertTrue(self.app.panel.isVisible())
        self.assertIn("マイク", self.app.panel.text.text())
        self.assertTrue((self.root / "microphone-error.json").exists())

    def test_speed_diagnostics_contain_no_transcript_or_target(self):
        session = SimpleNamespace(started_at=time.monotonic(), error="", metrics={"decode_count": 1})
        self.app.current_text = "保存対象外の認識本文"
        self.app.save_stream_diagnostics(session)
        result = json.loads((self.root / "stream-diagnostics.json").read_text())
        self.assertEqual(result["decode_count"], 1)
        self.assertNotIn("text", result)
        self.assertNotIn("app", result)
        self.assertNotIn("保存対象外", json.dumps(result, ensure_ascii=False))

    def test_failed_transfer_keeps_result_and_does_not_open_main(self):
        self.app.main.hide()
        self.app.phase = "finishing"
        self.app.session = SimpleNamespace(text="認識した本文。", error="")
        self.app.finish()
        self.assertEqual(self.app.main.editor.toPlainText(), "認識した本文。")
        self.assertFalse(self.app.main.isVisible())
        self.assertTrue(self.app.panel.isVisible())
        saved = json.loads((self.root / "last-input.json").read_text())
        self.assertEqual(saved["text"], "認識した本文。")
        self.assertEqual(saved["outcome"], "notAttempted")
        self.app.native.send_paste.assert_not_called()

    def drive_paste(self, receipts, *, sent=True, safe=True, target=None, fresh=...):
        self.app.phase = "idle"
        self.app.main.hide()
        self.app.panel.show()
        self.app.native.modifiers_up.return_value = True
        self.app.native.prepare_paste.return_value = safe
        self.app.native.send_paste.return_value = sent
        self.app.native.paste_receipt.side_effect = receipts
        target = target or SimpleNamespace(app="editor.exe", pid=123)
        self.app.native.capture.return_value = target if fresh is ... else fresh
        clock, queue = [10.0], []
        def schedule(ms, callback):
            queue.append((clock[0]+ms/1000, callback))
        with patch("shigaraki_win.app.QTimer.singleShot", side_effect=schedule), \
             patch("shigaraki_win.app.time.monotonic", side_effect=lambda: clock[0]):
            self.app.paste("認識した本文。", target)
            iterations = 0
            while self.app.phase == "pasting" and queue:
                queue.sort(key=lambda row: row[0])
                clock[0], callback = queue.pop(0)
                callback()
                iterations += 1
                self.assertLess(iterations, 200)
        return json.loads((self.root / "last-input.json").read_text())

    def test_only_confirmed_matching_paste_is_remembered(self):
        target = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value="前後",
                                 prefix="前", suffix="後", app="editor.exe")
        fresh = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value="前認識した本文。後",
                                prefix="前認識した本文。", suffix="後", app="editor.exe")
        with patch.object(self.app.context_memory, "remember", wraps=self.app.context_memory.remember) as remember:
            self.drive_paste(lambda *_: True, target=target, fresh=fresh)
            remember.assert_called_once_with(fresh, "認識した本文。")
        fresh.runtime_id = (9, 9)
        with patch.object(self.app.context_memory, "remember") as remember:
            self.drive_paste(lambda *_: True, target=target, fresh=fresh)
            remember.assert_not_called()
        self.assertIsNone(self.app.last_target)

    def test_confirmed_topic_can_be_reused_for_asr_with_proofreading_disabled(self):
        self.app.main.proof_enabled.setChecked(False)
        target = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value="前後",
                                 prefix="前", suffix="後", app="editor.exe")
        fresh = SimpleNamespace(hwnd=10, pid=20, runtime_id=(1, 2), value="前認識した本文。後",
                                prefix="前認識した本文。", suffix="後", app="editor.exe")
        with patch.object(self.app.context_memory, "remember", wraps=self.app.context_memory.remember) as remember:
            self.drive_paste(lambda *_: True, target=target, fresh=fresh)
            remember.assert_called_once_with(fresh, "認識した本文。")
        self.app.save_settings()
        self.assertIn("認識した本文。", self.app.context_memory.context_for(fresh))

    def test_unverified_and_failed_pastes_clear_context(self):
        for receipts, safe, sent in [(lambda *_: None, True, True),
                                    (lambda *_: False, True, True),
                                    (lambda *_: None, False, True),
                                    (lambda *_: None, True, False)]:
            self.app.context_memory._recent = "以前の話題"
            self.drive_paste(receipts, safe=safe, sent=sent)
            self.assertEqual(self.app.context_memory._recent, "")

    def test_reference_diagnostics_do_not_include_the_reference_text(self):
        self.app.context_source, self.app.context_chars = "field", 20
        self.drive_paste(lambda *_: None, safe=False)
        diagnostic = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertEqual(diagnostic["reference_source"], "field")
        self.assertEqual(diagnostic["reference_chars"], 20)
        self.assertEqual(set(k for k in diagnostic if k.startswith("reference_")),
                         {"reference_source", "reference_chars"})

    def test_unreadable_paste_receipt_is_quiet_and_preserves_original(self):
        self.app.raw_text = "認識した原文。"
        saved = self.drive_paste(lambda *_: None)
        self.assertEqual(saved["outcome"], "sentUnverified")
        self.assertEqual(saved["raw_text"], "認識した原文。")
        self.assertFalse(self.app.panel.isVisible())
        self.assertFalse(self.app.main.isVisible())
        self.assertEqual(self.app.main.status.text(), "貼り付け操作を送りました。入力先をご確認ください。")
        self.app.native.send_paste.assert_called_once()

    def test_compatible_target_pastes_once_and_records_route_without_false_receipt(self):
        target = SimpleNamespace(app="Antigravity.exe", pid=123, paste_mode="clipboard")
        self.app.native.last_target_reason = "captured_clipboard_value"
        self.app.native.last_prepare_reason = "ready"
        saved = self.drive_paste(lambda *_: None, target=target)
        self.assertEqual(saved["outcome"], "sentUnverified")
        self.app.native.send_paste.assert_called_once()
        self.assertEqual(self.app.native.prepare_paste.call_count, 2)
        self.assertFalse(self.app.panel.isVisible())
        diagnostic = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertEqual(diagnostic["transfer_mode"], "clipboard")
        self.assertEqual(diagnostic["prepare_check"], "ready")
        self.assertNotIn("認識した本文", json.dumps(diagnostic, ensure_ascii=False))

    def test_compatible_target_is_rechecked_after_changing_clipboard(self):
        target = SimpleNamespace(app="Antigravity.exe", pid=123, paste_mode="clipboard")
        self.app.native.prepare_paste.side_effect = [True, False]
        saved = self.drive_paste(lambda *_: None, target=target)
        self.assertEqual(saved["outcome"], "notAttempted")
        self.app.native.send_paste.assert_not_called()
        self.assertEqual(self.app.native.prepare_paste.call_count, 2)

    def test_delayed_readback_is_confirmed_without_repasting(self):
        count = [0]
        def delayed(*_):
            count[0] += 1
            return True if count[0] > 40 else False
        saved = self.drive_paste(delayed)
        self.assertEqual(saved["outcome"], "confirmed")
        self.assertFalse(self.app.panel.isVisible())
        self.app.native.send_paste.assert_called_once()

    def test_stale_readable_field_is_not_reported_as_a_failed_paste(self):
        saved = self.drive_paste(lambda *_: False)
        self.assertEqual(saved["outcome"], "sentUnverified")
        self.assertFalse(self.app.panel.isVisible())
        diagnostic = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertEqual(diagnostic["readback"], "unchanged")
        self.assertNotIn("認識した本文", json.dumps(diagnostic, ensure_ascii=False))
        self.app.native.send_paste.assert_called_once()

    def test_incomplete_send_is_not_a_success_or_a_missing_target(self):
        saved = self.drive_paste(lambda *_: None, sent=False)
        self.assertEqual(saved["outcome"], "sendFailed")
        self.assertTrue(self.app.panel.isVisible())
        self.app.native.paste_receipt.assert_not_called()
        self.app.native.send_paste.assert_called_once()

    def test_paste_records_model_readiness_and_time_without_text(self):
        self.app.engine = SimpleNamespace(ready=True)
        self.app.proof_engine = SimpleNamespace(ready=False)
        self.app.main.proof_enabled.setChecked(True)
        self.app.preparation_completed_at = 8
        self.app.correction_metrics = {"sentences_checked": 2, "suggestions_rejected": 1,
                                       "max_context_chars": 40}
        self.drive_paste(lambda *_: None)
        saved = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertTrue(saved["asr_ready"])
        self.assertTrue(saved["proof_enabled"])
        self.assertFalse(saved["proof_ready"])
        self.assertAlmostEqual(saved["seconds_after_preparation"], 2.025)
        self.assertEqual(saved["attempt_in_run"], 1)
        self.assertEqual(saved["proofreading"]["sentences_checked"], 2)
        self.assertEqual(saved["proofreading"]["suggestions_rejected"], 1)
        self.assertEqual(saved["proofreading"]["max_context_chars"], 40)
        self.assertNotIn("認識した本文", json.dumps(saved, ensure_ascii=False))

    def test_paste_before_preparation_has_no_invented_elapsed_time(self):
        self.drive_paste(lambda *_: None, safe=False)
        saved = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertIsNone(saved["seconds_after_preparation"])

    def test_transfer_attempt_number_advances_without_automatic_retries(self):
        self.drive_paste(lambda *_: None, safe=False)
        self.drive_paste(lambda *_: None, safe=False)
        saved = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertEqual(saved["attempt_in_run"], 2)
        self.assertEqual([row["attempt_in_run"] for row in saved["recent_attempts"]], [1, 2])
        self.app.native.send_paste.assert_not_called()

    def test_fallback_diagnostics_record_the_boundary_without_editor_contents(self):
        self.app.native.last_target_reason = "captured_clipboard_value"
        self.app.native.last_capture_fallback = "editable_document"
        self.app.native.last_capture_error = "COMError"
        self.drive_paste(lambda *_: None)
        saved = json.loads((self.root / "paste-diagnostics.json").read_text())
        self.assertEqual(saved["capture_fallback_from"], "editable_document")
        self.assertEqual(saved["capture_error_type"], "COMError")
        self.assertNotIn("認識した本文", json.dumps(saved, ensure_ascii=False))

    def test_changed_target_still_aborts_before_send(self):
        saved = self.drive_paste(lambda *_: None, safe=False)
        self.assertEqual(saved["outcome"], "notAttempted")
        self.app.native.send_paste.assert_not_called()
        self.assertTrue(self.app.panel.isVisible())

    def test_pending_proofreading_wait_is_bounded_and_cannot_rewrite_after_paste(self):
        self.app.phase = "finishing"
        self.app.session = SimpleNamespace(text="認識した本文。", error="")
        proof = MagicMock(); proof.has_pending.return_value = True
        proof.freeze.return_value = ("認識した本文。", "認識した本文。")
        proof.metrics = {"pending_at_stop": 1}
        self.app.proofreader = proof
        self.app.finish()
        self.assertEqual(self.app.phase, "finishing")
        self.app.native.send_paste.assert_not_called()
        self.app.proof_wait_until = time.monotonic()-1
        self.app.finish()
        proof.freeze.assert_called_once()
        self.assertIsNone(self.app.proofreader)
        self.assertEqual(self.app.phase, "idle")
        self.app.proofread_caption(self.app.generation)
        self.assertEqual(self.app.main.editor.toPlainText(), "認識した本文。")

    def test_fast_punctuation_does_not_wait_for_pending_generative_correction(self):
        self.app.phase="finishing"
        self.app.session=SimpleNamespace(text="明日は参加できますか",error="")
        proof=MagicMock();proof.has_pending.return_value=True
        proof.freeze.return_value=("明日は参加できますか","明日は参加できますか")
        proof.metrics={"pending_at_stop":1}
        self.app.proofreader=proof
        self.app.punctuation_engine=MagicMock()
        self.app.punctuation_engine.ready=True
        self.app.punctuation_engine.restore_recent.return_value="明日は参加できますか？"
        self.app.finish()
        self.assertEqual(self.app.phase,"idle")
        self.assertEqual(self.app.last_input,"明日は参加できますか？")
        self.assertEqual(self.app.raw_text,"明日は参加できますか")
        proof.has_pending.assert_not_called()
        proof.freeze.assert_called_once()
        self.app.punctuation_engine.restore_recent.assert_called_once()
        self.app.proofread_caption(self.app.generation)
        self.assertEqual(self.app.main.editor.toPlainText(),"明日は参加できますか？")

    def test_fast_punctuation_failure_keeps_original_and_still_finishes(self):
        self.app.phase="finishing"
        self.app.session=SimpleNamespace(text="参加費は1000円です",error="")
        self.app.punctuation_engine=MagicMock()
        self.app.punctuation_engine.ready=True
        self.app.punctuation_engine.restore_recent.side_effect=RuntimeError('fixture')
        self.app.finish()
        self.assertEqual(self.app.phase,"idle")
        self.assertEqual(self.app.last_input,"参加費は1000円です")
        self.assertEqual(self.app.raw_text,"参加費は1000円です")

    def test_quit_saves_draining_recognition_before_closing_handles_and_event_loop(self):
        done=threading.Event()
        self.app.session=SimpleNamespace(done=done,text="終了前の認識結果",cancel=MagicMock())
        self.app.timer.start(20)
        with patch.object(self.app.qt,"quit") as quit_loop, patch.object(self.app.engine,"close") as close_native:
            self.app.quit()
            self.app.tick()
            quit_loop.assert_not_called()
            close_native.assert_not_called()
            self.assertTrue(self.app.timer.isActive())
            done.set()
            self.app.tick()
            quit_loop.assert_called_once()
            self.app.tray.hide.assert_called_once()
            self.assertFalse(self.app.timer.isActive())
            close_native.assert_called_once()
            self.assertEqual(json.loads((self.root/"last-input.json").read_text())["text"],"終了前の認識結果")
            self.assertEqual(json.loads((self.root/"last-input.json").read_text())["raw_text"],"終了前の認識結果")

    def test_main_window_suppresses_panel_and_old_session_callbacks(self):
        self.app.panel.show()
        self.app.show_main()
        self.assertFalse(self.app.panel.isVisible())
        self.app.generation = 2
        self.app.phase = "recording"
        self.app.main.editor.setPlainText("今回の本文")
        self.app.session_caption(1, "前の録音")
        self.assertEqual(self.app.main.editor.toPlainText(), "今回の本文")

    def test_failure_keeps_partial_and_native_close_hides_window(self):
        self.app.phase = "finishing"
        self.app.session = SimpleNamespace(text="途中までの本文", error="fixtureError")
        self.app.finish()
        self.assertEqual(self.app.main.editor.toPlainText(), "途中までの本文")
        self.app.native.send_paste.assert_not_called()
        self.app.main.close()
        self.assertFalse(self.app.main.isVisible())

    def test_minimized_main_is_not_a_visible_caption_surface(self):
        self.app.show_main()
        self.assertTrue(self.app.main.presented())
        self.app.main.showMinimized()
        self.assertFalse(self.app.main.presented())

    def test_settings_can_scroll_to_the_bottom_without_reducing_the_font(self):
        from PySide6.QtWidgets import QScrollArea, QPushButton
        self.app.main.tabs.setCurrentIndex(2)
        self.app.main.resize(560, 540)
        self.app.main.show()
        self.qt.processEvents()
        scroll = self.app.main.tabs.currentWidget()
        self.assertIsInstance(scroll, QScrollArea)
        self.assertEqual(self.app.main.font().pointSize(), 12)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        button = next(b for b in scroll.findChildren(QPushButton)
                      if b.text() == "ログの保存場所を開く")
        scroll.ensureWidgetVisible(button)
        self.qt.processEvents()
        self.assertGreater(scroll.verticalScrollBar().value(), 0)
        self.assertTrue(scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().center())))
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

    def test_long_microphone_name_does_not_expand_the_settings_window(self):
        self.app.main.microphone.addItem("内蔵マイク配列の長い名前" * 12, 99)
        self.app.main.microphone.setCurrentIndex(self.app.main.microphone.count()-1)
        self.app.main.tabs.setCurrentIndex(2)
        self.app.main.resize(560, 540)
        self.app.main.show()
        self.qt.processEvents()
        self.assertLessEqual(self.app.main.width(), 560)
        scroll = self.app.main.tabs.currentWidget()
        self.assertLessEqual(self.app.main.microphone.width(), scroll.viewport().width())

    def test_recording_panel_is_restored_when_main_is_hidden_or_minimized(self):
        self.app.native.key_down.return_value = False
        self.app.phase = "recording"
        for minimized in (False, True):
            if minimized:
                self.app.main.showMinimized()
            else:
                self.app.main.hide()
            self.app.panel.hide()
            self.qt.processEvents()
            self.app.tick()
            self.assertTrue(self.app.panel.isVisible())
            self.assertEqual(self.app.phase, "recording")

    def test_recording_panel_stays_hidden_while_main_is_presented(self):
        self.app.native.key_down.return_value = False
        self.app.phase = "recording"
        self.app.main.showNormal()
        self.app.panel.show()
        self.qt.processEvents()
        self.app.tick()
        self.assertFalse(self.app.panel.isVisible())

    def test_setup_only_finishes_after_model_readiness(self):
        self.app.settings["setup_complete"] = False
        self.app.setup.show()
        self.app.complete_setup()
        self.assertTrue(self.app.setup.isVisible())
        self.app.prepared(True)
        self.assertTrue(self.app.settings["setup_complete"])
        self.app.complete_setup()
        self.assertFalse(self.app.setup.isVisible())
        self.assertTrue(self.app.main.presented())

    def test_preparation_time_is_recorded_without_transcript(self):
        self.app.initialized_at = 90
        self.app.preparation_started_at = 100
        self.app.preparation_metrics = {"asr_load_warmup_seconds": 3.0}
        self.app.proof_engine = MagicMock(); self.app.proof_engine.ready = True
        with patch("shigaraki_win.app.time.monotonic", return_value=108):
            self.app.prepared(True)
        saved = json.loads((self.root / "startup-diagnostics.json").read_text())
        self.assertEqual(saved["preparation_seconds"], 8)
        self.assertEqual(saved["initialization_elapsed_seconds"], 18)
        self.assertTrue(saved["input_ready"])
        self.assertEqual(saved["asr_load_warmup_seconds"], 3)
        self.assertNotIn("text", saved)
        self.assertIsNone(self.app.preparation_started_at)
        self.assertEqual(self.app.preparation_completed_at, 108)

    def test_preparation_is_visible_without_starting_a_recording(self):
        self.app.main.hide(); self.app.setup.hide()
        self.app.native.key_down.return_value = False
        self.app.phase = "modelSetup"
        self.app.preparation_started_at = time.monotonic()-3
        self.app.tick()
        self.assertTrue(self.app.panel.isVisible())
        self.assertIn("3秒", self.app.panel.text.text())
        self.assertIsNone(self.app.session)

    def test_only_current_utterance_completion_triggers_punctuation(self):
        self.app.generation = 3; self.app.phase = "recording"
        self.app.proofreader = MagicMock()
        self.app.proofreader.update.return_value = "明日は晴れです"
        self.app.session_utterance(2, "古い認識")
        self.app.proofreader.update.assert_not_called()
        self.app.session_utterance(3, "明日は晴れです")
        self.app.proofreader.update.assert_called_once_with("明日は晴れです", final=True)
        self.assertEqual(self.app.raw_text, "明日は晴れです")

    def test_installer_launch_opens_main_without_another_setup_click(self):
        self.app.from_installer = True
        self.app.settings["setup_complete"] = False
        self.app.proof_engine = MagicMock()
        self.app.proof_engine.ready = True
        self.app.setup.show()
        self.app.prepared(True)
        self.assertTrue(self.app.settings["setup_complete"])
        self.assertFalse(self.app.from_installer)
        self.assertFalse(self.app.setup.isVisible())
        self.assertTrue(self.app.main.presented())

    def test_installer_does_not_complete_if_opted_in_qwen_is_not_ready(self):
        self.enable_optional_proofreading()
        self.app.from_installer = True
        self.app.settings["setup_complete"] = False
        self.app.setup.show()
        self.app.prepared(True)
        self.assertFalse(self.app.settings["setup_complete"])
        self.assertFalse(self.app.setup.ready)
        self.assertEqual(self.app.setup.progress.value(), 0)
        self.assertTrue(self.app.setup.isVisible())
        self.assertTrue(self.app.from_installer)

    def test_installer_policy_block_still_opens_asr_with_clear_status(self):
        self.enable_optional_proofreading()
        self.app.from_installer = True
        self.app.settings["setup_complete"] = False
        self.app.proof_blocked = True
        self.app.setup.show()
        self.app.prepared(True)
        self.assertTrue(self.app.settings["setup_complete"])
        self.assertFalse(self.app.from_installer)
        self.assertTrue(self.app.main.presented())
        self.assertIn("0xC0E90002", self.app.main.status.text())
        self.assertIn("利用不可", self.app.main.proof_enabled.text())
        self.assertTrue(self.app.main.proof_enabled.isChecked())

    def test_model_prepare_button_is_an_explicit_policy_retry(self):
        with patch.object(self.app, "prepare") as prepare:
            self.app.main.prepare_model.emit()
        prepare.assert_called_once_with(retry_proof=True)

    def test_installed_models_do_not_override_an_existing_correction_preference(self):
        self.app.main.proof_enabled.setChecked(False)
        self.app.from_installer = True
        self.app.settings["setup_complete"] = False
        self.app.setup.show()
        self.app.prepared(True)
        self.assertFalse(self.app.main.proof_enabled.isChecked())
        self.assertTrue(self.app.settings["setup_complete"])
        self.assertFalse(self.app.setup.isVisible())

    def test_empty_recognition_failure_keeps_the_previous_result_available(self):
        self.app.last_input = "前回の認識結果"
        self.app.phase = "finishing"
        self.app.session = SimpleNamespace(text="", error="fixtureFailure")
        self.app.finish()
        self.assertEqual(self.app.main.editor.toPlainText(), "前回の認識結果")
        self.assertTrue(self.app.main.copy_button.isEnabled())


class InstallerTests(unittest.TestCase):
    def test_installer_only_copies_its_payload_and_keeps_user_data(self):
        from shigaraki_win.install_portable import copy_payload
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "installed"
            (source / "runtime").mkdir(parents=True)
            (source / "runtime" / "pythonw.exe").write_bytes(b"runtime fixture")
            (source / "unrelated.txt").write_text("must not copy")
            destination.mkdir()
            (destination / "user-note.txt").write_text("preserve")
            copy_payload(source, destination)
            self.assertEqual((destination / "runtime" / "pythonw.exe").read_bytes(), b"runtime fixture")
            self.assertFalse((destination / "unrelated.txt").exists())
            self.assertEqual((destination / "user-note.txt").read_text(), "preserve")


    def test_copy_reports_bytes_and_cancel_stops_before_next_file(self):
        from shigaraki_win.install_portable import copy_payload
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source/runtime").mkdir(parents=True)
            (root / "source/runtime/a").write_bytes(b"123")
            (root / "source/runtime/b").write_bytes(b"4567")
            cancel = threading.Event()
            updates = []
            def progress(done, total, name):
                updates.append((done, total, name))
                cancel.set()
            with self.assertRaises(InterruptedError):
                copy_payload(root / "source", root / "installed", progress, cancel)
            self.assertEqual(updates[0][:2], (3, 7))
            self.assertTrue((root / "installed/runtime/a").exists())
            self.assertFalse((root / "installed/runtime/b").exists())

    def test_shortcut_timeout_does_not_block_launch(self):
        import subprocess
        from shigaraki_win.install_portable import install
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.setup_models.prepare_both"), patch("shigaraki_win.install_portable.verify_installed_models"), patch("shigaraki_win.install_portable.subprocess.run", side_effect=subprocess.TimeoutExpired("fixture", 15)) as helper, patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                self.assertFalse(install(root / "source", root / "installed", lambda *_: None, threading.Event(), root / "log"))
                self.assertEqual(helper.call_args.kwargs["timeout"], 15)
                launch.assert_called_once()
            self.assertTrue((root / "log/shortcut-error.txt").exists())

    def test_cancelled_install_does_not_create_shortcut_or_launch(self):
        from shigaraki_win.install_portable import install
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("shigaraki_win.install_portable.copy_payload"), patch("shigaraki_win.install_portable.subprocess.run") as helper, patch("shigaraki_win.install_portable.subprocess.Popen") as launch:
                with self.assertRaises(InterruptedError):
                    install(root, root / "installed", lambda *_: None, cancel, root / "log")
                helper.assert_not_called()
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()

"""Model-free checks for the opt-in WAV benchmark and text output boundary."""
import base64
from contextlib import redirect_stderr
import importlib.util
import io
import os
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    "haas_benchmark", Path(__file__).resolve().parents[1] / "scripts/benchmark_haas_flow.py")
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class FakeWorker:
    ready = True
    process = type("Process", (), {"pid": os.getpid()})()

    def __init__(self):
        self._messages = queue.Queue()
        self.commands = []
        self.pcm = b""

    def begin(self, keywords, *, preview):
        self.commands.append({"type": "start", "keywords": keywords, "preview": preview})
        self._messages.put({"type": "caption", "id": 1, "text": "合成試験", "final": False,
                            "_bench_received_ns": time.monotonic_ns()})
        return 1

    def _send(self, messages):
        self.commands.extend(messages)
        for message in messages:
            if message["type"] == "audio":
                self.pcm += base64.b64decode(message["pcm"])
            elif message["type"] == "stop":
                self._messages.put({"type": "complete", "id": 1, "text": "合成試験",
                                    "_bench_received_ns": time.monotonic_ns()})


class BenchmarkTests(unittest.TestCase):
    def case(self):
        pcm = b"\1\0" * 937  # Full 800-sample packet plus a final 137 samples.
        return {"name": "synthetic", "wav": "memory", "pcm": pcm, "pcm_sha256": benchmark.sha(pcm)}

    def test_realtime_final_tail_and_text_opt_in(self):
        worker, case = FakeWorker(), self.case()
        unavailable = {"error": "unavailable", "total_seconds": None}
        with patch.object(benchmark, "cpu_snapshot", return_value=unavailable):
            text, row = benchmark.measure(worker, case, "Term=ターム")
        self.assertEqual(worker.pcm, case["pcm"])
        self.assertEqual(worker.commands[0], {"type": "start", "keywords": "Term=ターム", "preview": True})
        self.assertEqual([c["type"] for c in worker.commands], ["start", "audio", "audio", "stop"])
        self.assertEqual(row["sent_samples"], 937)
        self.assertEqual(text, "合成試験")
        self.assertNotIn("text", row)
        self.assertTrue(all("text" not in event for event in row["events"]))
        self.assertIsNone(row["worker_cpu"]["total_seconds"])
        anchors = row["anchors_seconds"]
        self.assertGreaterEqual(anchors["flush_start"], anchors["audio_virtual_end"])
        self.assertGreaterEqual(anchors["stop_send_start"], anchors["flush_complete"])
        self.assertAlmostEqual(row["audio_end_to_complete_seconds"], anchors["complete_receive"] - anchors["audio_virtual_end"])

    def test_include_text_requires_explicit_option(self):
        _, row = benchmark.measure(FakeWorker(), self.case(), "Term=ターム", include_text=True)
        self.assertEqual(row["text"], "合成試験")
        self.assertTrue(all(event["text"] == "合成試験" for event in row["events"]))

    def test_optional_wakeup_counters_preserve_unsupported(self):
        before = {"user_seconds": 1, "system_seconds": 2, "total_seconds": 3,
                  "pkg_idle_wakeups": 4, "interrupt_wakeups": 10, "error": None}
        after = {**before, "total_seconds": 4, "pkg_idle_wakeups": 7, "interrupt_wakeups": 12}
        delta = benchmark.cpu_difference(before, after)
        self.assertEqual(delta["pkg_idle_wakeups"], 3)
        self.assertEqual(delta["interrupt_wakeups"], 2)
        after["pkg_idle_wakeups"] = None
        self.assertIsNone(benchmark.cpu_difference(before, after)["pkg_idle_wakeups"])

    def test_windows_linked_worker_gets_no_library(self):
        library = benchmark.resolve_library(None, "win32")
        self.assertIsNone(library)
        Worker = benchmark.import_worker(Path(__file__).resolve().parents[1])
        worker = Worker(Path("worker.exe"), Path("model.gguf"), 1, library)
        self.assertIsNone(worker.native_library)
        with self.assertRaisesRegex(ValueError, "omit --library"):
            benchmark.resolve_library(Path("speech.dll"), "win32")
        with self.assertRaisesRegex(ValueError, "required"):
            benchmark.resolve_library(None, "darwin")

    def test_invalid_wav_size_rejected_before_reading_pcm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.wav"
            path.touch()
            fake = MagicMock()
            fake.getnchannels.return_value = 1
            fake.getsampwidth.return_value = 2
            fake.getframerate.return_value = 16000
            fake.getcomptype.return_value = "NONE"
            for frames in (0, 305 * 16000 + 1):
                fake.getnframes.return_value = frames
                with patch.object(benchmark.wave, "open") as opened:
                    opened.return_value.__enter__.return_value = fake
                    with self.assertRaisesRegex(ValueError, "4880000 frames"):
                        benchmark.read_case({"name": "test", "wav": str(path)})
                fake.readframes.assert_not_called()

    def test_nonfinite_durations_rejected_before_worker_start(self):
        args = ["benchmark", "--worker", "missing-worker", "--model", "missing-model",
                "--audio", "missing.wav", "--keywords-file", "missing-keywords", "--output", "unused.json"]
        for option in ("--timeout", "--warmup-seconds"):
            for value in ("nan", "inf", "-inf"):
                stderr = io.StringIO()
                with patch.object(benchmark.sys, "argv", args + [option + "=" + value]), \
                        patch.object(benchmark, "import_worker") as imported, redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as raised:
                        benchmark.main()
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("finite and positive", stderr.getvalue())
                imported.assert_not_called()


if __name__ == "__main__":
    unittest.main()

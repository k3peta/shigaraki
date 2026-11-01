import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from shigaraki_win.haas_session import CaptionSession, Segments, SAMPLE_RATE, MAX_WINDOW
from shigaraki_win.haas_worker import SpeechWorker, keywords_from_rules
from shigaraki_win.vocabulary import effective_words


def voice(seconds):
    return (.15*np.sin(np.arange(round(seconds*16000))*2*np.pi*220/16000)).astype(np.float32)


class Recorder:
    def __init__(self, audio, available=None):
        self.audio = audio
        self.available = len(audio) if available is None else available

    @property
    def recorded_seconds(self):
        return self.available/16000

    def read_audio(self, start, limit):
        return self.audio[start:min(self.available, start+limit)]


class Engine:
    def __init__(self):
        self.lengths = []

    def decode_window(self, audio, **kwargs):
        self.lengths.append(len(audio))
        return "認識" + str(len(self.lengths)) + "。"


class StreamingTests(unittest.TestCase):
    def test_caption_appears_before_stop_and_pending_text_can_be_revised(self):
        engine = Engine(); recorder = Recorder(voice(2), 12000)
        updates = []; event = threading.Event()
        session = CaptionSession(engine, recorder, lambda t: (updates.append(t), event.set()))
        session.start()
        try:
            self.assertTrue(event.wait(2))
            self.assertFalse(session.stop_requested.is_set())
            self.assertEqual(updates[0], "認識1。")
            event.clear(); recorder.available = 32000
            self.assertTrue(event.wait(2))
            self.assertEqual(updates[-1], "認識2。")
            self.assertNotIn("認識1。認識2。", updates)
        finally:
            session.stop(); self.assertTrue(session.done.wait(2))
        self.assertFalse(session.error)
        # Stopping with no new audio reuses the already computed full draft.
        self.assertEqual(len(engine.lengths), 2)

    def test_slow_decode_skips_stale_preview_requests_without_skipping_audio(self):
        entered, release, updated = threading.Event(), threading.Event(), threading.Event()
        engine = Engine(); recorder = Recorder(voice(4), 12000)
        original = engine.decode_window
        def decode(audio, **kwargs):
            if not engine.lengths:
                entered.set(); self.assertTrue(release.wait(2))
            return original(audio, **kwargs)
        engine.decode_window = decode
        session = CaptionSession(engine, recorder, lambda _: updated.set())
        session.start()
        try:
            self.assertTrue(entered.wait(2))
            recorder.available = 48000
            release.set(); self.assertTrue(updated.wait(2))
            session.stop(); self.assertTrue(session.done.wait(2))
            self.assertEqual(len(engine.lengths), 2)
            self.assertGreaterEqual(engine.lengths[0], 8800)
            self.assertEqual(engine.lengths[1], 48000)
            self.assertEqual(session.last_samples, 48000)
        finally:
            release.set(); session.cancel(); session.done.wait(2)

    def test_continuous_speech_is_bounded_and_all_audio_reaches_final_requests(self):
        audio = voice(30); engine = Engine(); completed = []
        session = CaptionSession(engine, Recorder(audio), lambda _: None, on_utterance=completed.append)
        session.stop(); session.run()
        self.assertFalse(session.error)
        self.assertTrue(all(n <= MAX_WINDOW for n in engine.lengths))
        self.assertEqual(sum(engine.lengths), len(audio))
        self.assertEqual(len(completed), 3)
        self.assertEqual(session.metrics["decode_count"], 3)
        self.assertTrue(completed[-1].startswith(completed[0]))

    def test_silence_never_invokes_asr(self):
        engine = Engine(); session = CaptionSession(engine, Recorder(np.zeros(64000)), lambda _: self.fail("silence emitted"))
        session.stop(); session.run()
        self.assertEqual(engine.lengths, [])
        self.assertEqual(session.text, "")

    def test_long_window_prefers_a_short_pause_without_losing_or_repeating_samples(self):
        audio = np.concatenate((voice(9), np.zeros(3200), voice(10)))
        received = []
        engine = Engine()
        def decode(value, **kwargs):
            received.append(value.copy()); return "文字。"
        engine.decode_window = decode
        session = CaptionSession(engine, Recorder(audio), lambda _: None)
        session.stop(); session.run()
        self.assertFalse(session.error)
        self.assertTrue(all(len(v) <= MAX_WINDOW for v in received))
        self.assertLess(len(received[0]), MAX_WINDOW)
        np.testing.assert_array_equal(np.concatenate(received), audio)

    def test_cancel_never_publishes_late_inference(self):
        started = threading.Event(); engine = Engine()
        def decode(audio, *, cancel, **kwargs):
            started.set(); self.assertTrue(cancel.wait(2)); return "遅れて届いた文字"
        engine.decode_window = decode
        updates = []; session = CaptionSession(engine, Recorder(voice(1)), updates.append)
        session.start(); self.assertTrue(started.wait(2)); session.cancel()
        self.assertTrue(session.done.wait(2)); self.assertEqual(updates, [])

    def test_empty_final_result_preserves_draft_as_failed_instead_of_success(self):
        engine = Engine(); recorder = Recorder(voice(2), 12000)
        original = engine.decode_window
        def decode(audio, **kwargs):
            if engine.lengths:
                return ""
            return original(audio, **kwargs)
        engine.decode_window = decode
        event = threading.Event(); session = CaptionSession(engine, recorder, lambda _: event.set())
        session.start(); self.assertTrue(event.wait(2))
        recorder.available = 32000; session.stop(); self.assertTrue(session.done.wait(2))
        self.assertEqual(session.text, "認識1。")
        self.assertEqual(session.error, "EmptyFinalResult")

    def test_nan_audio_fails_without_inference_or_success(self):
        engine = Engine(); session = CaptionSession(engine, Recorder(np.full(10000, np.nan)), lambda _: None)
        session.stop(); session.run()
        self.assertEqual(session.error, "ValueError"); self.assertEqual(engine.lengths, [])

    def test_pause_makes_distinct_utterances_without_pasting_or_stopping(self):
        audio = np.concatenate((voice(1), np.zeros(16000), voice(1)))
        engine = Engine(); completed = []
        session = CaptionSession(engine, Recorder(audio), lambda _: None, on_utterance=completed.append)
        session.stop(); session.run()
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[-1], "認識1。認識2。")


class DictionaryTests(unittest.TestCase):
    def test_human_readings_are_sent_with_explicitly_registered_school_names(self):
        result = keywords_from_rules(effective_words([["しがらき", "信楽"],
            ["おうびりん", "桜美林"], ["おうびりんだいがく", "桜美林大学"],
            ["わせだだいがく", "早稲田大学"]]))
        self.assertIn("信楽=シガラキ", result)
        self.assertIn("桜美林大学=", result)
        self.assertIn("オウビリンダイガク", result)
        self.assertIn("早稲田大学=ワセダダイガク", result)
        self.assertNotIn("\n桜美林=", "\n"+result)

    def test_dictionary_is_bounded_and_cannot_inject_more_lines_or_options(self):
        result = keywords_from_rules([(f"よみ{i}", f"単語{i}") for i in range(80)]+[("はーす", "bad\nkeywords_guard=false"),("えー", "x=y")])
        self.assertLessEqual(len(result), 615); self.assertLessEqual(len(result.splitlines()), 16)
        self.assertNotIn("keywords_guard", result); self.assertNotIn("x=y", result)

    def test_explicit_user_reading_takes_effect_and_context_ranks_it_first(self):
        result = keywords_from_rules(effective_words([["こーでっくす", "Codex"]]), ["Codex"])
        self.assertTrue(result.startswith("Codex=コーデックス"))


FIXTURE = r'''
import sys,json,time
mode=sys.argv[1]
print(json.dumps({'type':'ready','protocol':4,'version':'0.10.0','model':{'name':'Haas-ja-v1.0','device':'CPU'}}),flush=True)
if mode=='frozen':
 time.sleep(30)
else:
 for line in sys.stdin:
  v=json.loads(line)
  if v['type']!='transcribe':continue
  if mode=='slow':time.sleep(.3)
  if mode=='invalid':print('invalid-json',flush=True);continue
  if 'words' in v:raise RuntimeError('obsolete words field')
  if v.get('keywords') and v.get('keywords_guard') is not True:raise RuntimeError('missing guard')
  if v.get('keywords') and v.get('keywords_alpha')!=1.0:raise RuntimeError('unsafe default bias')
  print(json.dumps({'type':'end','id':v['id'],'text':v.get('keywords','成功')}),flush=True)
'''


class WorkerProcessTests(unittest.TestCase):
    def fixture(self, mode):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        path = Path(folder.name)/"fixture.py"; path.write_text(FIXTURE)
        real = subprocess.Popen
        def spawn(args, **kwargs):
            kwargs["creationflags"] = 0
            return real([sys.executable, "-u", str(path), mode], **kwargs)
        patcher = patch("shigaraki_win.haas_worker.subprocess.Popen", side_effect=spawn)
        patcher.start(); self.addCleanup(patcher.stop)
        worker = SpeechWorker(Path(sys.executable), Path(folder.name)/"model", 2)
        self.addCleanup(worker.close)
        worker.start(); return worker

    def test_protocol_and_guard_use_new_keywords_and_reuse_resident_process(self):
        worker = self.fixture("normal"); pid = worker.process.pid
        self.assertEqual(worker.transcribe(voice(.5), keywords="信楽=シガラキ"), "信楽=シガラキ")
        self.assertEqual(worker.transcribe(voice(.5)), "成功")
        self.assertEqual(pid, worker.process.pid)

    def test_cancelled_result_is_drained_before_the_next_request(self):
        worker = self.fixture("slow"); cancel = threading.Event()
        timer = threading.Timer(.1, cancel.set); timer.start()
        try:
            with self.assertRaises(InterruptedError):worker.transcribe(voice(.5), cancel=cancel)
        finally:timer.join()
        self.assertTrue(worker.ready)
        self.assertEqual(worker.transcribe(voice(.5)), "成功")

    def test_frozen_stdin_is_cancellable_and_child_and_io_threads_end(self):
        worker = self.fixture("frozen"); process = worker.process; cancel = threading.Event()
        timer = threading.Timer(.1, cancel.set); timer.start(); before = time.monotonic()
        try:
            with self.assertRaises(InterruptedError):worker.transcribe(voice(8), cancel=cancel)
        finally:timer.join()
        self.assertLess(time.monotonic()-before, 3)
        self.assertIsNotNone(process.poll())
        self.assertFalse(worker._writer.is_alive()); self.assertFalse(worker._reader.is_alive())

    def test_invalid_worker_output_fails_and_terminates_child(self):
        worker = self.fixture("invalid"); process = worker.process
        with self.assertRaises(RuntimeError):worker.transcribe(voice(.5))
        self.assertIsNotNone(process.poll()); self.assertFalse(worker.ready)


if __name__ == "__main__":unittest.main()

"""Exercise the real raw-pipe adapter, including stop/cancel/failure boundaries."""

import base64
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
from shigaraki_win.rust_worker import RustWorker
from shigaraki_win.rust_session import CaptionSession

FIXTURE = r"""
import json,sys,time
mode=sys.argv[1];current=0;count=0
print(json.dumps(dict(type='ready',protocol=1,engine='haas-rust',device='CPU')),flush=True)
if mode=='frozen':time.sleep(30)
else:
 for line in sys.stdin:
  v=json.loads(line);kind=v['type']
  if kind=='start':current=v['id'];count=0;continue
  if kind=='audio':
   import base64
   count+=len(base64.b64decode(v['pcm']))//2
   if mode=='invalid':print('bad-json',flush=True);continue
   print(json.dumps(dict(type='caption',id=current,text='認識'+str(count),final=True)),flush=True)
  if kind=='stop':
   if mode=='error':print(json.dumps(dict(type='error',id=current,code='native_decode',text='')),flush=True)
   else:print(json.dumps(dict(type='complete',id=current,text='認識'+str(count))),flush=True)
  if kind=='cancel':
   print(json.dumps(dict(type='cancelled',id=current,text='保管した原文')),flush=True)
   print(json.dumps(dict(type='caption',id=current,text='遅れて届いた旧結果',final=False)),flush=True)
"""


class Recorder:
    def __init__(self, size=16000):
        self.audio = np.full(size, 0.05, dtype=np.float32)
        self.available = size

    def read_audio(self, start, limit):
        return self.audio[start : min(start + limit, self.available)]


class Engine:
    def __init__(self, worker):
        self.worker = worker
        self.rules = []

    @contextmanager
    def session_worker(self, cancel=None):
        yield self.worker


class RustAdapterTests(unittest.TestCase):
    def fixture(self, mode="normal"):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        script = Path(folder.name) / "fixture.py"
        script.write_text(FIXTURE)
        real = subprocess.Popen

        def spawn(args, **kw):
            return real([sys.executable, "-u", str(script), mode], **kw)

        with patch("shigaraki_win.rust_worker.subprocess.Popen", side_effect=spawn):
            w = RustWorker(Path(sys.executable), Path(folder.name) / "model")
            w.start()
        self.addCleanup(w.close)
        return w

    def test_caption_and_utterance_do_not_stop_recording_then_stop_flushes_tail(self):
        w = self.fixture()
        r = Recorder(16017)
        r.available = 16000
        event = threading.Event()
        updates = []
        utterances = []
        s = CaptionSession(
            Engine(w),
            r,
            lambda t: (updates.append(t), event.set()),
            on_utterance=utterances.append,
        )
        s.start()
        self.assertTrue(event.wait(2))
        self.assertFalse(s.done.is_set())
        self.assertFalse(s.stop_requested.is_set())
        self.assertEqual(utterances, ["認識16000"])
        r.available = 16017
        s.stop()
        self.assertTrue(s.done.wait(2))
        self.assertEqual(s.text, "認識16017")
        self.assertFalse(s.error)
        self.assertEqual(s.last_samples, 16017)

    def test_cancel_drains_old_id_and_keeps_raw_text_without_late_publication(self):
        w = self.fixture()
        engine = Engine(w)
        event = threading.Event()
        updates = []
        first = CaptionSession(
            engine, Recorder(), lambda t: (updates.append(t), event.set())
        )
        first.start()
        self.assertTrue(event.wait(2))
        first.cancel()
        self.assertTrue(first.done.wait(2))
        self.assertEqual(updates, ["認識16000"])
        self.assertEqual(first.text, "保管した原文")
        self.assertFalse(first.error)
        second = CaptionSession(engine, Recorder(17000), lambda _: None)
        second.stop()
        second.run()
        self.assertFalse(second.error)
        self.assertEqual(second.text, "認識17000")
        self.assertNotIn("旧結果", second.text)

    def test_empty_error_retains_recognized_text_for_copy(self):
        w = self.fixture("error")
        s = CaptionSession(Engine(w), Recorder(), lambda _: None)
        s.stop()
        s.run()
        self.assertEqual(s.text, "認識16000")
        self.assertEqual(s.error, "native_decode")

    def test_invalid_protocol_keeps_failure_and_closes_reader_and_writer(self):
        w = self.fixture("invalid")
        p = w.process
        s = CaptionSession(Engine(w), Recorder(), lambda _: None)
        s.stop()
        s.run()
        self.assertTrue(s.error)
        self.assertIsNotNone(p.poll())
        self.assertFalse(w._reader.is_alive())
        self.assertFalse(w._writer.is_alive())

    def test_frozen_input_can_cancel_without_hanging_or_leaking_child(self):
        w = self.fixture("frozen")
        p = w.process
        event = threading.Event()
        s = CaptionSession(Engine(w), Recorder(16000 * 10), lambda _: None)
        s.start()
        time.sleep(0.1)
        before = time.monotonic()
        s.cancel()
        self.assertTrue(s.done.wait(4))
        self.assertLess(time.monotonic() - before, 4)
        self.assertIsNotNone(p.poll())
        self.assertFalse(w._reader.is_alive())
        self.assertFalse(w._writer.is_alive())

    def test_nan_fails_before_audio_is_sent(self):
        w = self.fixture()
        r = Recorder()
        r.audio[:] = np.nan
        s = CaptionSession(Engine(w), r, lambda _: None)
        s.stop()
        s.run()
        self.assertEqual(s.error, "ValueError")
        self.assertEqual(s.last_samples, 0)


if __name__ == "__main__":
    unittest.main()

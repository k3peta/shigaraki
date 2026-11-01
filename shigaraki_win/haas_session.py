"""Show revisable Haas text while recording; commit only bounded utterances."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time

SAMPLE_RATE = 16000
FRAME = 320
MAX_WINDOW = 12 * SAMPLE_RATE
SILENCE = int(.6 * SAMPLE_RATE)
PREROLL = int(.12 * SAMPLE_RATE)


@dataclass
class Segment:
    start: int
    end: int
    voiced: int


class Segments:
    """Energy-gated utterance boundaries. No recording samples are discarded."""
    def __init__(self):
        self.cursor = 0
        self.start = None
        self.voice_end = 0
        self.voiced = 0
        self.floor = 0
        self.pending = deque()
        self.quiet_start = None
        self.quiet_voiced = 0
        self.cut = None

    def feed(self, samples):
        import numpy as np
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not np.isfinite(audio).all():
            raise ValueError("non-finite audio")
        for offset in range(0, len(audio), FRAME):
            frame = audio[offset:offset+FRAME]
            end = self.cursor + len(frame)
            voiced = float(np.sqrt(np.mean(frame*frame))) >= .004
            if voiced:
                if self.start is None:
                    self.start = max(self.floor, self.cursor-PREROLL)
                    self.voiced = 0
                    self.cut = None
                if (self.quiet_start is not None and self.cursor-self.quiet_start >= PREROLL
                        and self.quiet_start-self.start >= 8*SAMPLE_RATE):
                    self.cut = ((self.quiet_start+self.cursor)//(2*FRAME)*FRAME, self.quiet_voiced)
                self.quiet_start = None
                self.voice_end = end
                self.voiced += len(frame)
            elif self.quiet_start is None:
                self.quiet_start = self.cursor
                self.quiet_voiced = self.voiced
            self.cursor = end
            if self.start is None:
                continue
            if end-self.start >= MAX_WINDOW:
                # Prefer a recently observed short pause over a cut inside a name.
                # Exact contiguous boundaries retain every sample in continuous speech.
                cut, voiced_before = self.cut or (end, self.voiced)
                self.pending.append(Segment(self.start, cut, voiced_before))
                self.floor = cut
                self.start = cut if cut < end else None
                self.voiced -= voiced_before
                self.cut = None
            elif end-self.voice_end >= SILENCE:
                last = min(end, self.voice_end+int(.12*SAMPLE_RATE))
                self.pending.append(Segment(self.start, last, self.voiced))
                self.floor = end
                self.start, self.voiced = None, 0
                self.cut = None

    def finish(self):
        if self.start is not None:
            self.pending.append(Segment(self.start, self.cursor, self.voiced))
            self.start, self.voiced = None, 0

    def draft(self):
        if self.start is None:
            return None
        return Segment(self.start, self.cursor, self.voiced)


class CaptionSession:
    def __init__(self, engine, recorder, emit, *, on_utterance=lambda _: None, phrases=()):
        self.engine, self.recorder, self.emit = engine, recorder, emit
        self.on_utterance = on_utterance
        self.phrases = tuple(phrases)
        self.cancelled = threading.Event()
        self.stop_requested = threading.Event()
        self.done = threading.Event()
        self.text, self.error = "", ""
        self.last_samples = 0
        self._thread = None
        self.started_at = time.monotonic()
        self.metrics = {"engine": "haas-ja-v1.0-q4_k", "chunk_seconds": .65,
            "first_caption_seconds": None, "caption_updates": 0, "decode_count": 0,
            "decode_seconds": 0.0, "last_decode_seconds": 0.0, "max_decode_seconds": 0.0,
            "last_audio_seconds": 0.0, "processed_audio_seconds": 0.0, "max_backlog_seconds": 0.0,
            "max_window_seconds": 0.0, "pending_segments_max": 0}

    def start(self):
        if self._thread is not None:
            raise RuntimeError("session already started")
        self._thread = threading.Thread(target=self.run, daemon=True, name="HaasCaptions")
        self._thread.start()

    def stop(self):
        self.stop_requested.set()

    def cancel(self):
        self.cancelled.set(); self.stop_requested.set()

    def _read(self, start, length):
        if hasattr(self.recorder, "read_audio"):
            return self.recorder.read_audio(start, length)
        value = self.recorder.snapshot_audio()
        return None if value is None else value[start:start+length]

    def _publish(self, value):
        if self.cancelled.is_set() or value == self.text:
            return
        self.text = value
        if value:
            self.metrics["caption_updates"] += 1
            if self.metrics["first_caption_seconds"] is None:
                self.metrics["first_caption_seconds"] = time.monotonic()-self.started_at
        self.emit(value)

    def run(self):
        segmenter = Segments()
        committed = ""
        last_decode = float("-inf")
        cached = None
        cached_text = ""
        draft_start, draft_text = None, ""
        sealed = False
        try:
            while not self.cancelled.is_set():
                stopping = self.stop_requested.is_set()
                # Coalesce all newly captured audio BEFORE starting the next decode.
                # Slow inference never produces a queue of stale preview requests.
                while True:
                    audio = self._read(self.last_samples, SAMPLE_RATE*20)
                    if audio is None or not len(audio):
                        break
                    if not stopping:
                        audio = audio[:len(audio)//FRAME*FRAME]
                        if not len(audio):
                            break
                    segmenter.feed(audio)
                    self.last_samples += len(audio)
                if stopping and not sealed:
                    segmenter.finish(); sealed = True
                self.metrics["pending_segments_max"] = max(self.metrics["pending_segments_max"], len(segmenter.pending))
                final = bool(segmenter.pending)
                segment = segmenter.pending[0] if final else segmenter.draft()
                if segment is None:
                    if stopping:
                        break
                    self.stop_requested.wait(.025)
                    continue
                if segment.voiced < int(.10*SAMPLE_RATE):
                    if final:
                        segmenter.pending.popleft()
                    else:
                        self.stop_requested.wait(.025)
                    continue
                key = (segment.start, segment.end)
                now = time.monotonic()
                if not final and (segment.end-segment.start < int(.55*SAMPLE_RATE)
                        or now-last_decode < .65 or key == cached):
                    self.stop_requested.wait(.025)
                    continue
                if segment.start != draft_start:
                    draft_start, draft_text = segment.start, ""
                if key == cached:
                    text = cached_text
                else:
                    audio = self._read(segment.start, segment.end-segment.start)
                    if audio is None or len(audio) != segment.end-segment.start:
                        raise RuntimeError("captured audio unavailable")
                    before = time.monotonic()
                    text = self.engine.decode_window(audio, cancel=self.cancelled, phrases=self.phrases)
                    elapsed = time.monotonic()-before
                    last_decode = before
                    cached, cached_text = key, text
                    self.metrics.update(decode_count=self.metrics["decode_count"]+1,
                        decode_seconds=self.metrics["decode_seconds"]+elapsed,
                        last_decode_seconds=elapsed, max_decode_seconds=max(self.metrics["max_decode_seconds"], elapsed),
                        last_audio_seconds=len(audio)/SAMPLE_RATE,
                        max_window_seconds=max(self.metrics["max_window_seconds"], len(audio)/SAMPLE_RATE),
                        processed_audio_seconds=segment.end/SAMPLE_RATE)
                    captured = getattr(self.recorder, "recorded_seconds", self.last_samples/SAMPLE_RATE)
                    if isinstance(captured, (float, int)):
                        self.metrics["max_backlog_seconds"] = max(self.metrics["max_backlog_seconds"],
                            max(0, captured-segment.end/SAMPLE_RATE))
                if self.cancelled.is_set():
                    break
                if final and not text and draft_text:
                    # Retain the recoverable draft, but never auto-paste a phrase
                    # that the final recognition rejected (for example noise).
                    self.error = "EmptyFinalResult"
                    self._publish(committed+draft_text)
                    break
                if text:
                    draft_text = text
                self._publish(committed+draft_text)
                if final:
                    committed += draft_text
                    if draft_text:
                        self.on_utterance(committed)
                    segmenter.pending.popleft()
                    draft_start, draft_text = None, ""
        except InterruptedError:
            if not self.cancelled.is_set():
                self.error = "InterruptedError"
        except Exception as error:
            self.error = type(error).__name__
        finally:
            self.done.set()

from __future__ import annotations

import threading
import re
import time
from contextlib import ExitStack


def replace_words(text, rules):
    mappings = {heard: written for heard, written in rules if heard and written}
    if not mappings:
        return text
    expression = "|".join(re.escape(key) for key in sorted(mappings, key=len, reverse=True))
    return re.sub(expression, lambda match: mappings[match.group()], text)


def format_text(text, rules):
    text = replace_words(text, rules)
    protected = {written for _, written in rules if written}
    commands = {"てん": "、", "テン": "、", "点": "、",
                "まる": "。", "マル": "。", "丸": "。"}
    names = "|".join(commands)
    def standalone(match):
        word = match.group(1)
        return match.group(0) if word in protected else commands[word]
    text = re.sub(r"(?:^|[\s。、]+)("+names+r")(?:[\s。、]+|$)", standalone, text)
    # A completed grammatical ending is also a clear command boundary. Do not
    # globally replace syllables inside words such as 収まる or てんとう虫.
    def ending(match):
        word = match.group(2)
        return match.group(0) if word in protected else match.group(1)+commands[word]
    return re.sub(r"(です|ます|でした|ました|ください)("+names+r")[\s。、]*", ending, text)


class CaptionSession:
    """Feed each captured sample once into one stateful native stream."""

    def __init__(self, engine, recorder, emit, *, on_utterance=lambda _: None, phrases=()):
        self.engine, self.recorder, self.emit = engine, recorder, emit
        self.on_utterance = on_utterance
        self.phrases = tuple(phrases)
        self.cancelled = threading.Event()
        self.stop_requested = threading.Event()
        self.done = threading.Event()
        self.text = ""
        self.last_samples = 0
        self.error = ""
        self._thread = None
        self.started_at = time.monotonic()
        self.metrics = {"engine": "nemotron-3.5", "chunk_seconds": .32,
                        "first_caption_seconds": None, "caption_updates": 0,
                        "decode_count": 0, "decode_seconds": 0.0,
                        "last_decode_seconds": 0.0, "max_decode_seconds": 0.0,
                        "last_audio_seconds": 0.0, "processed_audio_seconds": 0.0,
                        "max_backlog_seconds": 0.0}

    def start(self):
        if self._thread is not None:
            raise RuntimeError("session already started")
        self._thread = threading.Thread(target=self.run, daemon=True, name="ShigarakiCPU")
        self._thread.start()

    def stop(self):
        self.stop_requested.set()

    def cancel(self):
        self.cancelled.set()
        self.stop_requested.set()

    def _read(self):
        if hasattr(self.recorder, "read_audio"):
            return self.recorder.read_audio(self.last_samples, 5120)
        audio = self.recorder.snapshot_audio()
        return None if audio is None else audio[self.last_samples:self.last_samples+5120]

    def run(self):
        import numpy as np
        committed = ""
        processed_origin = 0.0
        def publish(results):
            nonlocal committed
            for value, final, processed in results:
                if self.cancelled.is_set():
                    return
                self.metrics["processed_audio_seconds"] = processed_origin + processed
                text = committed + value
                if text != self.text:
                    self.text = text
                    if text:
                        self.metrics["caption_updates"] += 1
                        if self.metrics["first_caption_seconds"] is None:
                            self.metrics["first_caption_seconds"] = time.monotonic()-self.started_at
                    self.emit(text)
                if final:
                    committed = text
                    if text:
                        self.on_utterance(text)
        speech_seen = False
        quiet_samples = 0
        try:
            with ExitStack() as streams:
                stream = streams.enter_context(self.engine.stream(phrases=self.phrases)
                    if self.phrases else self.engine.stream())
                while not self.cancelled.is_set():
                    stopping = self.stop_requested.is_set()
                    audio = self._read()
                    if audio is None or not len(audio):
                        if stopping:
                            if speech_seen:
                                stream.finish()
                                publish(stream.results(self.cancelled))
                            break
                        self.stop_requested.wait(.02)
                        continue
                    if len(audio) < 2560 and not stopping:
                        self.stop_requested.wait(.02)
                        continue
                    # Skip only leading digital/very quiet silence. Once speech
                    # starts, pauses remain in the stream to preserve its state.
                    if not np.isfinite(audio).all():
                        raise ValueError("non-finite audio")
                    self.last_samples += len(audio)
                    quiet = np.sqrt(np.mean(audio*audio)) < .001
                    speech_seen = speech_seen or not quiet
                    quiet_samples = quiet_samples + len(audio) if quiet else 0
                    if not speech_seen:
                        continue
                    before = time.monotonic()
                    stream.push(audio)
                    publish(stream.results(self.cancelled))
                    elapsed = time.monotonic()-before
                    self.metrics.update(decode_count=self.metrics["decode_count"]+1,
                        decode_seconds=self.metrics["decode_seconds"]+elapsed,
                        last_decode_seconds=elapsed,
                        max_decode_seconds=max(self.metrics["max_decode_seconds"], elapsed),
                        last_audio_seconds=len(audio)/16000)
                    captured = getattr(self.recorder, "recorded_seconds", self.last_samples/16000)
                    if isinstance(captured, (int,float)):
                        backlog = max(0, captured-self.last_samples/16000)
                        self.metrics["max_backlog_seconds"] = max(self.metrics["max_backlog_seconds"], backlog)
                    if quiet_samples >= 19200:
                        # Only rotate acoustic state inside an already-observed
                        # quiet pause. A fresh stream avoids losing the next
                        # onset after long silence; recording/paste keep running.
                        stream.finish()
                        publish(stream.results(self.cancelled))
                        streams.close()
                        if self.cancelled.is_set():
                            break
                        committed = self.text
                        processed_origin = self.last_samples / 16000
                        stream = streams.enter_context(self.engine.stream(phrases=self.phrases)
                            if self.phrases else self.engine.stream())
                        speech_seen = False
                        quiet_samples = 0
        except Exception as error:
            self.error = type(error).__name__
        finally:
            self.done.set()

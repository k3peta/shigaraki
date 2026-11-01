"""UI adapter only: Rust owns recognition, segmentation and preview scheduling."""

from __future__ import annotations
import base64
import queue
import threading
import time
from .haas_worker import keywords_from_rules


class CaptionSession:
    def __init__(
        self, engine, recorder, emit, *, on_utterance=lambda _: None, phrases=()
    ):
        self.engine, self.recorder, self.emit = engine, recorder, emit
        self.on_utterance, self.phrases = on_utterance, tuple(phrases)
        self.cancelled = threading.Event()
        self.stop_requested = threading.Event()
        self.done = threading.Event()
        self.text, self.error = "", ""
        self.last_samples = 0
        self._thread = None
        self.started_at = time.monotonic()
        self.metrics = {
            "engine": "haas-rust-q4_k",
            "chunk_seconds": 0.65,
            "first_caption_seconds": None,
            "caption_updates": 0,
            "decode_count": 0,
            "decode_seconds": 0.0,
            "last_decode_seconds": 0.0,
            "max_decode_seconds": 0.0,
            "last_audio_seconds": 0.0,
            "processed_audio_seconds": 0.0,
            "max_backlog_seconds": 0.0,
        }

    def start(self):
        if self._thread is not None:
            raise RuntimeError("session already started")
        self._thread = threading.Thread(
            target=self.run, daemon=True, name="HaasRustSession"
        )
        self._thread.start()

    def stop(self):
        self.stop_requested.set()

    def cancel(self):
        self.cancelled.set()
        self.stop_requested.set()

    def _read(self):
        if hasattr(self.recorder, "read_audio"):
            return self.recorder.read_audio(self.last_samples, 16000)
        audio = self.recorder.snapshot_audio()
        return (
            None
            if audio is None
            else audio[self.last_samples : self.last_samples + 16000]
        )

    def _publish(self, text):
        if text == self.text or self.cancelled.is_set():
            return
        self.text = text
        if text:
            self.metrics["caption_updates"] += 1
            if self.metrics["first_caption_seconds"] is None:
                self.metrics["first_caption_seconds"] = (
                    time.monotonic() - self.started_at
                )
        self.emit(text)

    def run(self):
        import numpy as np

        worker = None
        try:
            with self.engine.session_worker(self.cancelled) as worker:
                rid = worker.begin(
                    keywords_from_rules(self.engine.rules, self.phrases),
                    preview=not self.stop_requested.is_set(),
                    cancel=self.cancelled,
                )
                sequence = 0
                stopped = False
                cancelling = False
                deadline = None
                while True:
                    if self.cancelled.is_set() and not cancelling:
                        worker._send([{"type": "cancel", "id": rid}])
                        cancelling = True
                        deadline = time.monotonic() + 3
                    if not stopped and not cancelling:
                        stopping = self.stop_requested.is_set()
                        while True:
                            audio = self._read()
                            if (
                                audio is None
                                or not len(audio)
                                or len(audio) < 800
                                and not stopping
                            ):
                                break
                            if not np.isfinite(audio).all():
                                raise ValueError("non-finite audio")
                            pcm = (
                                (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                            )
                            worker._send(
                                [
                                    {
                                        "type": "audio",
                                        "id": rid,
                                        "seq": sequence,
                                        "pcm": base64.b64encode(pcm).decode("ascii"),
                                    }
                                ],
                                self.cancelled,
                            )
                            sequence += 1
                            self.last_samples += len(audio)
                        if stopping:
                            worker._send([{"type": "stop", "id": rid}])
                            stopped = True
                            deadline = time.monotonic() + 60
                    try:
                        message = worker._messages.get(timeout=0.02)
                    except queue.Empty:
                        if deadline is not None and time.monotonic() > deadline:
                            raise TimeoutError("Rust session timeout")
                        if not worker.ready:
                            raise RuntimeError("Rust worker exited")
                        continue
                    if isinstance(message, BaseException):
                        raise RuntimeError("Rust worker stopped") from message
                    if message.get("type") == "fatal":
                        raise RuntimeError("Rust protocol failed")
                    if message.get("id") != rid:
                        if type(message.get("id")) is int and 0 < message["id"] < rid:
                            continue
                        raise ValueError("invalid session identity")
                    if message.get("type") not in (
                        "caption",
                        "complete",
                        "cancelled",
                        "error",
                    ):
                        raise ValueError("invalid session event")
                    value = message.get("text", "")
                    if not isinstance(value, str):
                        raise ValueError("invalid caption")
                    metrics = message.get("metrics", {})
                    for key in (
                        "decode_count",
                        "decode_seconds",
                        "last_decode_seconds",
                        "max_decode_seconds",
                        "last_audio_seconds",
                        "processed_audio_seconds",
                        "max_window_seconds",
                        "max_backlog_seconds",
                        "pending_segments_max",
                    ):
                        if key in metrics and type(metrics[key]) in (int, float):
                            self.metrics[key] = metrics[key]
                    kind = message["type"]
                    if kind == "caption":
                        self._publish(value)
                        if (
                            message.get("final")
                            and not self.cancelled.is_set()
                            and value
                        ):
                            self.on_utterance(value)
                    elif kind in ("complete", "cancelled", "error"):
                        if kind == "error":
                            self.error = message.get("code", "RustDecodeError")
                            if not value:
                                value = self.text
                        if self.cancelled.is_set():
                            if value:
                                self.text = value
                        else:
                            self._publish(value)
                        break
        except InterruptedError:
            if not self.cancelled.is_set():
                self.error = "InterruptedError"
            if worker is not None:
                worker.close(kill=True)
        except Exception as error:
            self.error = type(error).__name__
            if worker is not None:
                worker.close(kill=True)
        finally:
            self.done.set()

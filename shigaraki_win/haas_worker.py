"""Resident CPU-only speech.cpp worker. One bounded request is in flight."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def keywords_from_rules(rules, phrases=()):
    """Pass dictionary spellings AND readings to Haas, with its guard enabled."""
    import re
    import unicodedata
    grouped = {}
    for heard, written in rules:
        if not isinstance(heard, str) or not isinstance(written, str):
            continue
        if not written or len(written) > 80 or any(c in written for c in "\r\n=,"):
            continue
        if any(unicodedata.category(c) in ("Cc", "Cs") for c in written):
            continue
        aliases = grouped.setdefault(written, [])
        kana = unicodedata.normalize("NFKC", heard).translate(
            str.maketrans({chr(c): chr(c+0x60) for c in range(0x3041, 0x3097)}))
        if re.fullmatch(r"[ァ-ヶー・]+", kana) and kana not in aliases:
            aliases.append(kana)
    preferred = [p for p in phrases if p in grouped]
    order = list(dict.fromkeys([*preferred[::-1], *list(grouped)[::-1]]))
    # A shorter registered prefix competes with its full registered name.
    order = [v for v in order if not any(v != other and v in other for other in order)]
    result, size = [], 0
    for written in order:
        aliases = sorted(grouped[written], key=lambda value: (len(value), value))[:3]
        line = written + ("=" + ",".join(aliases) if aliases else "")
        if size + len(line) > 600:
            continue
        result.append(line)
        size += len(line)
        if len(result) >= 16:
            break
    return "\n".join(result)


class SpeechWorker:
    def __init__(self, executable: Path, model: Path, threads=6):
        self.executable, self.model = Path(executable), Path(model)
        self.threads = threads
        self.process = None
        self._reader = self._writer = None
        self._messages = queue.Queue(maxsize=16)
        self._writes = queue.Queue(maxsize=1)
        self._sequence = 0
        self.info = {}

    @property
    def ready(self):
        return bool(self.info) and self.process is not None and self.process.poll() is None

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(1_048_577)
                if not line:
                    raise EOFError("speech worker exited")
                if len(line) > 1_048_576 or not line.endswith(b"\n"):
                    raise ValueError("oversized worker message")
                message = json.loads(line)
                if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                    raise ValueError("invalid worker message")
                if message["type"] == "progress":
                    continue
                self._messages.put_nowait(message)
        except BaseException as error:
            try:
                self._messages.put_nowait(error)
            except queue.Full:
                pass

    def _write(self):
        while True:
            item = self._writes.get()
            if item is None:
                return
            payload, done, result = item
            try:
                # bufsize=0 gives raw pipes, without BufferedWriter shutdown locks.
                view = memoryview(payload)
                while view:
                    count = self.process.stdin.write(view)
                    if not count:
                        raise BrokenPipeError("speech worker stopped reading")
                    view = view[count:]
            except BaseException as error:
                result.append(error)
            finally:
                done.set()

    def _send(self, messages, cancel=None):
        payload = b"".join(json.dumps(m, ensure_ascii=False).encode("utf-8")+b"\n" for m in messages)
        done, result = threading.Event(), []
        self._writes.put_nowait((payload, done, result))
        deadline = time.monotonic() + 8
        while not done.wait(.025):
            if cancel is not None and cancel.is_set():
                self.close(kill=True)
                raise InterruptedError("speech write cancelled")
            if time.monotonic() > deadline:
                self.close(kill=True)
                raise TimeoutError("speech write timeout")
        if result:
            raise OSError("speech worker pipe failed") from result[0]

    def _receive(self, deadline, cancel=None):
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("speech request cancelled")
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("speech worker response timeout")
            try:
                message = self._messages.get(timeout=min(.025, remaining))
            except queue.Empty:
                continue
            if isinstance(message, BaseException):
                code = self.process.poll() if self.process is not None else None
                detail = "" if code is None else f" (exit=0x{code & 0xffffffff:08X})"
                raise RuntimeError("speech worker stopped"+detail) from message
            return message

    def start(self, cancel=None):
        if self.ready:
            return
        if cancel is not None and cancel.is_set():
            raise InterruptedError("speech startup cancelled")
        self._messages = queue.Queue(maxsize=16)
        self._writes = queue.Queue(maxsize=1)
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
                 if sys.platform == "win32" else 0)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GGML_", "SPEECH_"))}
        self.process = subprocess.Popen([str(self.executable.resolve()), "worker", str(self.model.resolve()),
            "--device", "cpu", "--threads", str(self.threads)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, creationflags=flags, env=env,
            cwd=self.executable.parent)
        self._reader = threading.Thread(target=self._read, daemon=True, name="HaasProtocol")
        self._writer = threading.Thread(target=self._write, daemon=True, name="HaasWriter")
        self._reader.start(); self._writer.start()
        try:
            ready = self._receive(time.monotonic()+60, cancel)
            model = ready.get("model", {})
            if (ready.get("type") != "ready" or ready.get("protocol") != 4
                    or ready.get("version") != "0.10.0" or model.get("device") != "CPU"
                    or model.get("name") != "Haas-ja-v1.0"):
                raise RuntimeError("unexpected Haas CPU worker")
            self.info = ready
        except BaseException:
            self.close(kill=True)
            raise

    def transcribe(self, audio, *, keywords="", cancel=None, timeout=30):
        import numpy as np
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if len(samples) > 16_000*12 or not np.isfinite(samples).all():
            raise ValueError("invalid or oversized speech window")
        if not len(samples):
            return ""
        if not self.ready:
            raise RuntimeError("Haas worker is not ready")
        self._sequence += 1
        rid = str(self._sequence)
        pcm = (np.clip(samples, -1, 1)*32767).astype("<i2").tobytes()
        messages = [{"type": "chunk", "id": rid, "seq": 0, "pcm": base64.b64encode(pcm).decode("ascii")},
                    {"type": "transcribe", "id": rid, "sample_rate": 16000}]
        if keywords:
            # Alpha=2 corrected a synthetic university name but dropped adjacent
            # words. Keep a conservative bias for this comparative preview.
            messages[-1].update(keywords=keywords, keywords_alpha=1.0, keywords_guard=True)
        try:
            self._send(messages, cancel)
            result = self._receive(time.monotonic()+timeout, cancel)
            if result.get("id") != rid or result.get("type") != "end" or not isinstance(result.get("text"), str):
                raise RuntimeError("Haas transcription failed")
            return result["text"].strip()
        except InterruptedError:
            if not self.ready:
                raise
            # Drain the cancelled request so its result can never enter the next recording.
            try:
                self._send([{"type": "cancel", "id": rid}])
                message = self._receive(time.monotonic()+2)
                if message.get("id") != rid or message.get("type") not in ("cancelled", "end"):
                    raise RuntimeError("cancel response mismatch")
            except BaseException:
                self.close(kill=True)
            raise
        except BaseException:
            self.close(kill=True)
            raise

    def close(self, *, kill=False):
        process = self.process
        self.info = {}
        if process is None:
            return
        if kill and process.poll() is None:
            process.terminate()
        try:
            self._writes.put_nowait(None)
        except queue.Full:
            pass
        if self._writer:
            self._writer.join(timeout=.5)
        if not self._writer or not self._writer.is_alive():
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=2)
        if self._writer:
            self._writer.join(timeout=1)
        if self._reader:
            self._reader.join(timeout=1)
        process.stdin.close(); process.stdout.close()
        self.process = None

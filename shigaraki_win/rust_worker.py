"""Raw-pipe transport for the single Rust recognition controller."""

from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import queue
from .haas_worker import SpeechWorker


class RustWorker(SpeechWorker):
    def __init__(self, executable, model, threads=6, native_library=None):
        super().__init__(executable, model, threads)
        self.native_library = native_library

    def start(self, cancel=None):
        if self.ready:
            return
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Rust startup cancelled")
        self._messages = queue.Queue(maxsize=64)
        self._writes = queue.Queue(maxsize=1)
        command = [
            str(self.executable.resolve()),
            "--model",
            str(self.model.resolve()),
            "--threads",
            str(self.threads),
        ]
        if self.native_library is not None:
            command += ["--library", str(Path(self.native_library).resolve())]
        flags = (
            subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
            if sys.platform == "win32"
            else 0
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("GGML_", "SPEECH_", "DYLD_", "LD_PRELOAD"))
        }
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=flags,
            env=env,
            cwd=self.executable.parent,
        )
        self._reader = threading.Thread(
            target=self._read, daemon=True, name="HaasRustEvents"
        )
        self._writer = threading.Thread(
            target=self._write, daemon=True, name="HaasRustAudio"
        )
        self._reader.start()
        self._writer.start()
        try:
            info = self._receive(time.monotonic() + 60, cancel)
            if (
                info.get("type") != "ready"
                or info.get("protocol") != 1
                or info.get("engine") != "haas-rust"
                or info.get("device") != "CPU"
            ):
                raise RuntimeError("unexpected Rust CPU worker")
            self.info = info
        except BaseException:
            self.close(kill=True)
            raise

    def begin(self, keywords, *, preview=True, cancel=None):
        self._sequence += 1
        self._send(
            [
                {
                    "type": "start",
                    "id": self._sequence,
                    "keywords": keywords,
                    "preview": preview,
                }
            ],
            cancel,
        )
        return self._sequence

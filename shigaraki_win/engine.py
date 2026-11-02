from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Callable

MODEL_NAME = "Haas-616M-ja-v1.0-Q4_K.gguf"
MODEL_BYTES = 351_901_216
MODEL_SHA256 = "d24d53f29b75214a933ff482b0de1217e0a800f4118cca39ad6cf03609a89d6f"
MODEL_REVISION = "6b965d5007ba780585de51549d57a8c1c0d67c7e"
MODEL_URL = f"https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF/resolve/{MODEL_REVISION}/{MODEL_NAME}"
WINDOWS_WORKER_BYTES = 6_145_536
WINDOWS_WORKER_SHA256 = "0b9953a1a503158d842273d2ce61a8fff6a825056cf973772798b1a6cd1a21d5"


def data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "ShigarakiHaasPreview"


def default_model() -> Path:
    return data_dir() / "models" / MODEL_NAME


def valid_file(path: Path, size: int, sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == sha256


def valid_model(path: Path) -> bool:
    return valid_file(path, MODEL_BYTES, MODEL_SHA256)


def prepare_model(path: Path, progress: Callable[[int, int], None] = lambda *_: None,
                  *, cancel: threading.Event | None = None, repair: bool = False) -> Path:
    return fetch_model(path, progress, size=MODEL_BYTES, sha256=MODEL_SHA256, url=MODEL_URL,
                       owned_path=default_model(), cancel=cancel, repair=repair)


def fetch_model(path, progress, *, size, sha256, url, owned_path, cancel=None, repair=False):
    if valid_file(path, size, sha256):
        return path
    # Never replace an existing damaged/user-supplied file silently.
    if path.exists():
        if not repair or path.resolve() != owned_path.resolve():
            raise ValueError("model checksum mismatch")
        path.rename(path.with_name(f"{path.name}.invalid-{uuid.uuid4().hex}"))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        digest = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(url, timeout=15) as response, temporary.open("xb") as output:
            if not response.geturl().startswith("https://"):
                raise ValueError("model download requires HTTPS")
            for block in iter(lambda: response.read(1024 * 1024), b""):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("model preparation cancelled")
                count += len(block)
                if count > size:
                    raise ValueError("model download exceeds expected size")
                output.write(block)
                digest.update(block)
                progress(count, size)
        if count != size or digest.hexdigest() != sha256:
            raise ValueError("model checksum mismatch")
        # Refuse a concurrent writer instead of overwriting its file.
        os.link(temporary, path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


class CpuEngine:
    """One resident Haas CPU worker, shared by sequential preview requests."""

    def __init__(self, model: Path, *, library=None, executable=None, native_library=None):
        import sys
        self.path = Path(model)
        self.executable = Path(executable or library or Path(__file__).resolve().parent.parent / "speech" /
                               ("shigaraki-haas-flow.exe" if sys.platform == "win32" else "shigaraki-haas-flow"))
        self._bundled_executable = executable is None and library is None
        self.native_library = native_library
        self.library = self.executable
        self.threads = min(6, max(1, os.cpu_count() or 4))
        self._worker = None
        self._lock = threading.Lock()
        from .vocabulary import effective_words
        self.rules = effective_words([])

    @property
    def ready(self):
        return self._worker is not None and self._worker.ready

    def set_vocabulary(self, rules):
        self.rules = list(rules)

    def load(self, *, cancel=None):
        with self._lock:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Haas preparation cancelled")
            if self.ready:
                return
            if self._worker is not None:
                self._worker.close()
            if not valid_model(self.path):
                raise ValueError("Haas model missing or checksum mismatch")
            import sys
            if (sys.platform == "win32" and self._bundled_executable
                    and not valid_file(self.executable, WINDOWS_WORKER_BYTES, WINDOWS_WORKER_SHA256)):
                raise ValueError("Haas CPU executable missing or checksum mismatch")
            from .rust_worker import RustWorker
            worker = RustWorker(self.executable, self.path, self.threads, self.native_library)
            try:
                worker.start(cancel)
            except BaseException:
                worker.close(kill=True)
                raise
            self._worker = worker

    def session_worker(self, cancel=None):
        from contextlib import contextmanager
        @contextmanager
        def recording():
            self.load(cancel=cancel)
            with self._lock:
                yield self._worker
        return recording()

    def close(self):
        with self._lock:
            if self._worker is not None:
                self._worker.close()
                self._worker = None

    def transcribe(self, audio, *, cancel=None, phrases=()):
        from types import SimpleNamespace
        from .rust_session import CaptionSession
        import numpy as np
        samples = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not np.isfinite(samples).all():
            raise ValueError("non-finite audio")
        if (not len(samples) or (cancel is not None and cancel.is_set())
                or float(np.sqrt(np.mean(samples*samples))) < .001):
            return ""
        recorder = SimpleNamespace(read_audio=lambda start, limit: samples[start:start+limit],
                                   recorded_seconds=len(samples)/16000)
        self.load(cancel=cancel)
        session = CaptionSession(self, recorder, lambda _: None, phrases=phrases)
        if cancel is not None:
            session.cancelled = cancel
        session.stop(); session.run()
        if session.error:
            raise RuntimeError(session.error)
        return "" if session.cancelled.is_set() else session.text.strip()


def read_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise ValueError("expected mono 16 kHz PCM16 WAV")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0

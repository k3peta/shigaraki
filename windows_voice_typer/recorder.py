from __future__ import annotations

import os
import bisect
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any


class Recorder:
    def __init__(self, sample_rate: int = 16000, channels: int = 1, device: str | int | None = "auto"):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self._stream = None
        self._chunks = []
        self._offsets = []
        self._frames = 0
        self._chunks_lock = threading.Lock()
        self._level_lock = threading.Lock()
        self._level_percent = 0.0
        self._started_at = 0.0
        self._input_device_name = ""
        self.last_error = ""

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def input_device_name(self) -> str:
        return self._input_device_name

    @property
    def level_percent(self) -> float:
        with self._level_lock:
            return self._level_percent if self.is_recording else 0.0

    @property
    def recorded_seconds(self) -> float:
        with self._chunks_lock:
            frames = self._frames
        return frames / float(self.sample_rate)

    def start(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        self._chunks = []
        self._offsets = []
        self._frames = 0
        with self._level_lock:
            self._level_percent = 0.0
        self._started_at = time.monotonic()
        for device_index in _candidate_input_devices(sd, self.device):
            try:
                self._stream = sd.InputStream(
                    device=device_index,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    callback=self._callback,
                )
                self._stream.start()
                device = sd.query_devices(device_index)
                self._input_device_name = str(device.get("name", device_index))
                print(f"recording device: {device_index} {self._input_device_name}")
                return
            except Exception as error:
                self.last_error = str(error)
                failed_stream = self._stream
                self._stream = None
                if failed_stream is not None:
                    try:
                        failed_stream.close()
                    except Exception:
                        pass
        self._started_at = 0.0
        raise RuntimeError(f"Could not open input device: {self.last_error}")

    def stop_to_wav(self) -> Path:
        return self.audio_to_wav(self.stop_audio())

    def stop_audio(self) -> Any:
        """Stop capture and return PCM directly; streaming users need no WAV file."""
        if self._stream is None:
            raise RuntimeError("Recorder is not running")
        try:
            try:
                self._stream.stop()
            finally:
                self._stream.close()
        finally:
            self._stream = None
            self._started_at = 0.0
            with self._level_lock:
                self._level_percent = 0.0
        audio = self.snapshot_audio()
        if audio is None:
            raise RuntimeError("No audio was captured")
        return audio

    def snapshot_audio(self) -> Any | None:
        with self._chunks_lock:
            chunks = list(self._chunks)
        if not chunks:
            return None
        import numpy as np

        return np.concatenate(chunks, axis=0)

    def read_audio(self, start: int, limit: int) -> Any | None:
        """Copy only unconsumed PCM for stateful streaming, not the full history."""
        with self._chunks_lock:
            if start >= self._frames:
                return None
            index = max(0, bisect.bisect_right(self._offsets, start)-1)
            stop = min(self._frames, start+limit)
            slices = []
            while index < len(self._chunks) and self._offsets[index] < stop:
                offset, chunk = self._offsets[index], self._chunks[index]
                slices.append(chunk[max(0,start-offset):min(len(chunk),stop-offset)])
                index += 1
        import numpy as np
        return np.concatenate(slices, axis=0) if slices else None

    def audio_to_wav(self, audio: Any) -> Path:
        import numpy as np

        clipped = np.clip(audio, -1.0, 1.0)
        pcm = (clipped * 32767).astype(np.int16)
        fd, name = tempfile.mkstemp(prefix="koe-kichi-", suffix=".wav")
        os.close(fd)
        with wave.open(name, "wb") as handle:
            handle.setnchannels(self.channels)
            handle.setsampwidth(2)
            handle.setframerate(self.sample_rate)
            handle.writeframes(pcm.tobytes())
        return Path(name)

    def _callback(self, indata: Any, frames: int, _time_info: Any, status: Any) -> None:
        if status:
            print(f"recording status: {status}")
        try:
            rms = float((indata * indata).mean() ** 0.5)
            level = max(0.0, min(100.0, rms * 650.0))
        except Exception:
            level = 0.0
        with self._level_lock:
            self._level_percent = level
        with self._chunks_lock:
            self._offsets.append(self._frames)
            self._chunks.append(indata.copy())
            self._frames += int(indata.shape[0])


def _candidate_input_devices(sd: Any, configured: str | int | None) -> list[int | None]:
    if configured in (None, "", "auto"):
        default = sd.default.device
        # sounddevice returns a list-like _InputOutputPair, not a list/tuple.
        try:
            default_input = default[0]
        except (TypeError, IndexError, KeyError):
            default_input = default
        candidates: list[int | None] = []
        if default_input is not None and int(default_input) >= 0:
            candidates.append(int(default_input))
        for index, device in enumerate(sd.query_devices()):
            if int(device.get("max_input_channels", 0)) > 0 and index not in candidates:
                candidates.append(index)
        return candidates
    return [int(configured)]

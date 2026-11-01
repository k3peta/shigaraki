"""Small ctypes binding to NVIDIA NeMo-Speech.cpp's stable ASR C ABI v1."""
from __future__ import annotations

import ctypes as C
import os
from pathlib import Path
import sys


class Sized(C.Structure):
    def __init__(self, **values):
        super().__init__()
        self.size = C.sizeof(type(self))
        for name, value in values.items():
            setattr(self, name, value)


class Backend(Sized):
    _fields_ = [("size", C.c_size_t), ("gpu", C.c_int32)]


class Model(Sized):
    _fields_ = [("size", C.c_size_t), ("path", C.c_char_p), ("name", C.c_char_p)]


class Streaming(Sized):
    _fields_ = [("size", C.c_size_t), ("chunk_size", C.c_float),
                ("ctc_left_padding", C.c_float), ("ctc_right_padding", C.c_float),
                ("rnnt_right_context", C.c_int32)]


class Endpointing(Sized):
    _fields_ = [("size", C.c_size_t), ("enable", C.c_bool), ("vad_based", C.c_bool),
                ("stop_history_eou_ms", C.c_int32)]


class Config(Sized):
    _fields_ = [("size", C.c_size_t), ("backend", C.POINTER(Backend)),
                ("model", C.POINTER(Model)), ("streaming", C.POINTER(Streaming)),
                ("decoder", C.c_void_p), ("vad", C.c_void_p),
                ("endpointing", C.POINTER(Endpointing)), ("postproc", C.c_void_p),
                ("diar", C.c_void_p), ("batching", C.c_void_p)]


class SpeechContext(Sized):
    _fields_ = [("size", C.c_size_t), ("phrases", C.POINTER(C.c_char_p)),
                ("phrase_count", C.c_size_t), ("boost", C.c_float)]


class Options(Sized):
    _fields_ = [("size", C.c_size_t), ("request_id", C.c_char_p), ("language_code", C.c_char_p),
                ("interim_results", C.c_bool), ("enable_word_time_offsets", C.c_bool),
                ("enable_automatic_punctuation", C.c_bool), ("verbatim_transcripts", C.c_bool),
                ("profanity_filter", C.c_bool), ("stop_history_eou_ms", C.c_int32),
                ("speech_contexts", C.c_void_p), ("speech_context_count", C.c_size_t),
                ("max_alternatives", C.c_int32), ("enable_speaker_diarization", C.c_bool),
                ("max_speaker_count", C.c_int32)]


def default_library() -> Path:
    root = Path(__file__).resolve().parent.parent / "native"
    if sys.platform == "win32":
        return root / "bin" / "nemo_speech_asr_c.dll"
    if sys.platform == "darwin":
        return root / "lib" / "libnemo_speech_asr_c.dylib"
    return root / "lib" / "libnemo_speech_asr_c.so"


class Native:
    def __init__(self, library: Path):
        self._dll_search = []
        if sys.platform == "win32":
            self._dll_search.append(os.add_dll_directory(str(library.parent)))
        self.lib = C.CDLL(str(library.resolve()))
        signatures = {
            "create": ([C.POINTER(Config), C.POINTER(C.c_void_p)], C.c_int),
            "destroy": ([C.c_void_p], None),
            "recognition_options_default": ([], Options),
            "streaming_recognize": ([C.c_void_p, C.POINTER(Options), C.POINTER(C.c_void_p)], C.c_int),
            "stream_push_f32": ([C.c_void_p, C.POINTER(C.c_float), C.c_size_t, C.c_int32], C.c_int),
            "stream_finish": ([C.c_void_p], C.c_int),
            "stream_next": ([C.c_void_p, C.POINTER(C.c_void_p)], C.c_int),
            "stream_close": ([C.c_void_p], None),
            "result_is_final": ([C.c_void_p], C.c_bool),
            "result_audio_processed": ([C.c_void_p], C.c_float),
            "result_transcript": ([C.c_void_p, C.c_size_t], C.c_char_p),
            "result_destroy": ([C.c_void_p], None),
            "last_error": ([], C.c_char_p),
            "version": ([], C.c_char_p),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, "nemo_speech_asr_" + name)
            function.argtypes, function.restype = args, result
            setattr(self, name, function)

    def check(self, status):
        if status:
            detail = self.last_error()
            raise RuntimeError(detail.decode("utf-8", errors="replace") if detail else f"ASR status {status}")

    def load(self, path: Path, *, right_context=3):
        backend = Backend(gpu=-1)  # CPU, even when a graphics device is present.
        model = Model(path=str(path).encode("utf-8"))
        streaming = Streaming(chunk_size=.32, rnnt_right_context=right_context)
        # Finalize utterances after pauses, but keep the app's recording session
        # alive until its own stop gesture. Without this reset long pauses can
        # swallow the onset of the next utterance.
        endpointing = Endpointing(enable=True, stop_history_eou_ms=1000)
        config = Config(backend=C.pointer(backend), model=C.pointer(model),
                        streaming=C.pointer(streaming), endpointing=C.pointer(endpointing))
        handle = C.c_void_p()
        self.check(self.create(C.byref(config), C.byref(handle)))
        if not handle.value:
            raise RuntimeError("ASR returned no recognizer")
        return handle


class Stream:
    def __init__(self, native: Native, recognizer, *, phrases=()):
        self.native = native
        self.handle = C.c_void_p()
        options = native.recognition_options_default()
        options.language_code = b"ja-JP"
        options.interim_results = True
        options.enable_automatic_punctuation = True
        # Own the UTF-8 buffers for the stream lifetime, even though the pinned
        # C ABI copies request options during streaming_recognize.
        self._phrase_bytes = [phrase.encode("utf-8") for phrase in phrases]
        if self._phrase_bytes:
            self._phrases = (C.c_char_p * len(self._phrase_bytes))(*self._phrase_bytes)
            self._context = SpeechContext(phrases=self._phrases,
                phrase_count=len(self._phrase_bytes), boost=1.0)
            options.speech_contexts = C.cast(C.pointer(self._context), C.c_void_p)
            options.speech_context_count = 1
        native.check(native.streaming_recognize(recognizer, C.byref(options), C.byref(self.handle)))
        if not self.handle.value:
            raise RuntimeError("ASR returned no stream")
        self.finished = False

    def push(self, audio):
        import numpy as np
        if not self.handle.value or self.finished:
            raise RuntimeError("ASR stream is closed")
        samples = np.ascontiguousarray(audio, dtype=np.float32).reshape(-1)
        if not np.isfinite(samples).all():
            raise ValueError("non-finite audio")
        if len(samples):
            self.native.check(self.native.stream_push_f32(self.handle,
                samples.ctypes.data_as(C.POINTER(C.c_float)), len(samples), 16000))

    def finish(self):
        if self.handle.value and not self.finished:
            self.native.check(self.native.stream_finish(self.handle))
            self.finished = True

    def results(self, cancel=None):
        while self.handle.value and not (cancel is not None and cancel.is_set()):
            result = C.c_void_p()
            self.native.check(self.native.stream_next(self.handle, C.byref(result)))
            if not result.value:
                return
            try:
                raw = self.native.result_transcript(result, 0)
                text = raw.decode("utf-8", errors="replace") if raw else ""
                yield text, bool(self.native.result_is_final(result)), float(self.native.result_audio_processed(result))
            finally:
                self.native.result_destroy(result)

    def close(self):
        if self.handle.value:
            self.native.stream_close(self.handle)
            self.handle = C.c_void_p()

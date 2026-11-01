"""Prepare Haas and bundled punctuation; Qwen is an optional later opt-in."""
from __future__ import annotations
from . import engine
from .punctuation import PunctuationRuntime


def prepare_both(progress, cancel):
    if cancel.is_set():
        raise InterruptedError("model installation cancelled")
    progress("asr_model", 0, engine.MODEL_BYTES, engine.MODEL_NAME)
    engine.prepare_model(engine.default_model(),
        lambda done, total: progress("asr_model", done, total, engine.MODEL_NAME), cancel=cancel, repair=True)
    if cancel.is_set():
        raise InterruptedError("model installation cancelled")
    progress("asr_model", engine.MODEL_BYTES, engine.MODEL_BYTES, engine.MODEL_NAME)


def verify_both(progress, *, asr_library=None, proof_executable=None, allow_policy_block=False, require_punctuation=False):
    asr = engine.CpuEngine(engine.default_model(), library=asr_library)
    punctuation = PunctuationRuntime() if require_punctuation else None
    try:
        progress("check_asr"); asr.load()
        if not asr.ready:
            raise RuntimeError("Haas did not become ready")
        if punctuation is not None:
            progress("check_punctuation"); punctuation.load()
            if not punctuation.ready:
                raise RuntimeError("punctuation model did not become ready")
        progress("models_ready")
    finally:
        if punctuation is not None:
            punctuation.close()
        asr.close()

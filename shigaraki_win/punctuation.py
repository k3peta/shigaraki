"""Pinned, CPU-only non-generative punctuation. Original characters survive."""
from pathlib import Path
import hashlib
import re

MODEL_SHA = "8295f79d0e6c9b2ea8dd2276813a6e5fe121799aaf9a80def45456f28be41461"
TOKENIZER_SHA = "368d21a3ac020cf7795d4ebe1c7b66ab661fbded97c007250a828253905a803c"


def render(text, offsets, labels, protected=()):
    locked = []
    for expression in (r'https?://[^\s<>「」]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
                       r'「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`\n]+`',
                       r'[0-9０-９]+(?:[.,．][0-9０-９]+)*'):
        locked.extend((m.start(),m.end()) for m in re.finditer(expression,text))
    for term in protected:
        if term:
            locked.extend((m.start(),m.end()) for m in re.finditer(re.escape(term),text))
    marks = {}
    symbols = ("", "、", "。", "？", "")  # Exclamations are off by default.
    for (start,end), label in zip(offsets,labels):
        if not 0 <= start < end <= len(text) or not 0 <= int(label) < len(symbols):
            continue
        mark = symbols[int(label)]
        if (not mark or text[end-1].isspace() or text[end-1] in "、。？！!?"
                or end < len(text) and text[end] in "、。？！!?"
                or any(a < end < b for a,b in locked)):
            continue
        marks[end] = mark
    return "".join(char + marks.get(index+1, "") for index,char in enumerate(text))


class PunctuationRuntime:
    def __init__(self, model_dir=None):
        self.directory = Path(model_dir or Path(__file__).resolve().parent.parent / "punctuation")
        self.session = self.tokenizer = None

    @property
    def ready(self):
        return self.session is not None

    def load(self, *, cancel=None, retry_blocked=False):
        if self.ready:
            return
        model, tokenizer = self.directory/"punct.int8.onnx", self.directory/"tokenizer.json"
        for path, size, expected in ((model, 37174608, MODEL_SHA), (tokenizer, 6718593, TOKENIZER_SHA)):
            if path.stat().st_size != size:
                raise ValueError("punctuation model size mismatch")
            with path.open('rb') as source:
                actual = hashlib.file_digest(source,'sha256').hexdigest()
            if actual != expected:
                raise ValueError("punctuation model checksum mismatch")
        if cancel is not None and cancel.is_set():
            return
        from .runtime_status import loader_errors_as_status
        loader_errors_as_status()
        import onnxruntime as ort
        ort.disable_telemetry_events()
        from tokenizers import Tokenizer
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.session = ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
        if cancel is not None and cancel.is_set():
            self.close()

    def restore(self, text, *, protected=(), cancel=None):
        if not self.ready or not text or len(text) > 800 or cancel is not None and cancel.is_set():
            return text
        import numpy as np
        encoding = self.tokenizer.encode(text)
        logits = self.session.run(['logits'],{
            'input_ids':np.array([encoding.ids],dtype=np.int64),
            'attention_mask':np.ones((1,len(encoding.ids)),dtype=np.int64)})[0][0]
        if cancel is not None and cancel.is_set():
            return text
        probabilities = np.exp(logits-logits.max(-1,keepdims=True))
        probabilities /= probabilities.sum(-1,keepdims=True)
        scores = logits.copy()
        scores[:,4] = -np.inf
        scores[probabilities[:,1] < .8,1] = -np.inf
        scores[probabilities[:,3] < .9,3] = -np.inf
        return render(text,encoding.offsets,scores.argmax(-1),protected)

    def close(self):
        self.session = self.tokenizer = None

    def restore_recent(self,text,*,protected=(),limit=400):
        if not 1 <= limit <= 800:
            raise ValueError("punctuation window exceeds maximum")
        prefix,tail=text[:-limit],text[-limit:]
        return prefix+self.restore(tail,protected=protected)

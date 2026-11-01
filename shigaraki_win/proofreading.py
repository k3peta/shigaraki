"""Sentence-only local proofreading, separate from immediate ASR captions."""
from __future__ import annotations

import difflib
import re
import threading
import time


def sentences(text, boundaries=()):
    """Keep exact offsets/terminators; never process an unfinished tail."""
    start = 0
    ends = {match.end() for match in re.finditer(r"[。\n]", text)}
    ends.update(end for end in boundaries if 0 < end <= len(text))
    for end in sorted(ends):
        yield start, text[start:end]
        start = end


def safe_revision(original, candidate, protected=(), *, allow_punctuation=False):
    """Reject obvious loss, invention, changed quantities and dictionary edits.

    This is a conservative guard, not proof that two sentences mean the same.
    The untouched recognition text is retained regardless of acceptance.
    """
    if not isinstance(candidate, str):
        return original
    ending = "\n" if original.endswith("\n") else ""
    source = original.rstrip("\r\n")
    value = candidate.strip()
    if not value or value == source or len(value) > len(source) * 1.15 + 2:
        return original
    if "\n" in value or any(x in value for x in ("<think>", "</think>", "```")):
        return original
    if any(mark not in source and mark in value for mark in ("{", "}", "<|", "</")):
        return original
    for pattern in (r'https?://[^\s<>「」]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
                    r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"\n]*"|`[^`\n]+`'):
        if re.findall(pattern, source) != re.findall(pattern, value):
            return original
    numbers = r"[0-9０-９]+(?:[.,．][0-9０-９]+)*|[一二三四五六七八九十百千万億兆]+"
    negatives = r"なかった|ない|ません|ぬ|禁止|不要|不可能|未確認|未定|ず"
    for pattern in (numbers, negatives):
        if re.findall(pattern, source) != re.findall(pattern, value):
            return original
    if source.count("。") != value.count("。") and not (allow_punctuation and value.count("。") >= source.count("。")):
        return original
    if any(source.count(mark) != value.count(mark) for mark in '「」『』"（）()'):
        return original
    if re.sub(r"\s", "", source) == re.sub(r"\s", "", value):
        return original
    if protected:
        expression = "|".join(re.escape(term) for term in sorted(set(protected), key=len, reverse=True) if term)
        if expression:
            locked = re.findall(expression, source)
            kept = [term for term in re.findall(expression, value) if term in locked]
            if kept != locked:
                return original
        # A contextual dictionary term is not permission to invent a new place
        # or person. Its introduction must correspond to a similar local span.
        alignment = difflib.SequenceMatcher(None, source, value, autojunk=False).get_opcodes()
        def boundary(position, end=False):
            for tag, a, b, c, d in alignment:
                if c <= position <= d:
                    return a + position - c if tag == "equal" else b if end else a
            return len(source)
        localized, occupied = [], []
        for term in sorted(set(protected), key=len, reverse=True):
            if not term or term in source or term not in value:
                continue
            for match in re.finditer(re.escape(term), value):
                if any(left < match.end() and right > match.start() for left, right in occupied):
                    continue
                left, right = boundary(match.start()), boundary(match.end(), True)
                fragment = source[left:right]
                if difflib.SequenceMatcher(None, fragment, term, autojunk=False).ratio() < .55:
                    return original
                if any(a < right and b > left for a, b, _ in localized):
                    return original
                localized.append((left, right, term))
                occupied.append((match.start(), match.end()))
        if localized:
            # Apply just the approved dictionary spans to the original. A model
            # that also changes に to へ must not rewrite that unrelated particle.
            edits_to_apply = list(localized)
            for tag, a, b, c, d in alignment:
                addition = value[c:d]
                if tag == "insert" and addition and all(mark in "、。" for mark in addition):
                    if not any(left < a < right for left, right, _ in localized):
                        edits_to_apply.append((a, b, addition))
            value = source
            for a, b, replacement in sorted(edits_to_apply, reverse=True):
                value = value[:a] + replacement + value[b:]
    edits = 0
    old_text, new_text = source, value
    if allow_punctuation:
        for tag, a, b, _, _ in difflib.SequenceMatcher(None, source, value, autojunk=False).get_opcodes():
            if tag in ("delete", "replace") and any(mark in source[a:b] for mark in "、。"):
                return original
        old_text = re.sub("[、。]", "", source)
        new_text = re.sub("[、。]", "", value)
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        old, new = old_text[a:b], new_text[c:d]
        if tag == "delete" and old.strip(" \t、，"):
            return original
        if max(b-a, d-c) > 6:
            return original
        edits += max(b-a, d-c)
    if edits > max(2, len(source) * .16):
        return original
    return value + ending


class SentenceProofreader:
    """One event-driven worker; new captions never wait on language inference."""

    def __init__(self, engine, emit, *, protected=(), debounce=.35, context="", vocabulary=()):
        self.engine, self.emit = engine, emit
        self.protected, self.debounce = tuple(protected), debounce
        self.context = context[-240:]
        self._context_deadline = time.monotonic() + 180
        self.vocabulary = tuple(vocabulary)
        self._condition = threading.Condition()
        self._raw = ""
        self._boundaries = set()
        self._changed = 0.0
        self._since = {}
        self._revisions = {}
        self._attempted = set()
        self._closed = False
        self.cancelled = threading.Event()
        self.metrics = {"sentences_checked": 0, "sentences_changed": 0, "errors": 0,
                        "suggestions": 0, "suggestions_rejected": 0,
                        "max_context_chars": 0,
                        "max_seconds": 0.0, "pending_at_stop": 0}
        self._thread = threading.Thread(target=self._run, daemon=True, name="ShigarakiProofread")
        self._thread.start()

    def _key(self, text, start, sentence):
        previous = text[max(0, start-240):start]
        reference = (self.context + "\n" + previous).strip()[-240:]
        return start, sentence, reference

    def _sentences(self):
        return sentences(self._raw, self._boundaries)

    def _reference_at(self, start):
        previous, end = "", 0
        for offset, sentence in self._sentences():
            if offset >= start:
                break
            revised = self._revisions.get(self._key(self._raw, offset, sentence), sentence)
            previous = (previous + self._raw[end:offset] + revised)[-240:]
            end = offset + len(sentence)
        reference = self.context if time.monotonic() <= self._context_deadline else ""
        return (reference + "\n" + previous + self._raw[end:start]).strip()[-240:]

    def _render(self):
        parts, end = [], 0
        for start, sentence in self._sentences():
            key = self._key(self._raw, start, sentence)
            parts.append(self._revisions.get(key, sentence))
            end = start + len(sentence)
        return "".join(parts) + self._raw[end:]

    def update(self, text, *, final=False):
        with self._condition:
            if not self._closed and text != self._raw:
                self._boundaries = {end for end in self._boundaries
                    if end <= len(text) and self._raw[:end] == text[:end]}
                self._raw, self._changed = text, time.monotonic()
                for start, sentence in self._sentences():
                    self._since.setdefault(self._key(text, start, sentence), self._changed)
                self._condition.notify()
            if final and not self._closed:
                if self._raw:
                    self._boundaries.add(len(self._raw))
                for start, sentence in self._sentences():
                    # Zero is a real monotonic timestamp on freshly booted
                    # machines; subtract the debounce to make final text due now.
                    self._since[self._key(self._raw, start, sentence)] = time.monotonic() - self.debounce
                self._condition.notify()
            return self._render()

    def has_pending(self):
        with self._condition:
            return any(3 <= len(sentence.strip()) <= 240 and
                       self._key(self._raw, start, sentence) not in self._revisions
                       for start, sentence in self._sentences())

    def snapshot(self):
        with self._condition:
            return self._raw, self._render()

    def freeze(self):
        with self._condition:
            self._closed = True
            self.cancelled.set()
            self.metrics["pending_at_stop"] = sum(
                self._key(self._raw, start, sentence) not in self._revisions
                for start, sentence in self._sentences() if sentence.strip("。\n "))
            self._condition.notify()
            return self._raw, self._render()

    def _run(self):
        while True:
            with self._condition:
                while True:
                    if self._closed:
                        return
                    pending = [(start, sentence, self._key(self._raw, start, sentence))
                               for start, sentence in self._sentences()
                               if self._key(self._raw, start, sentence) not in self._attempted]
                    delay = (self._since[pending[0][2]] + self.debounce - time.monotonic()) if pending else 0
                    if pending and delay <= 0:
                        start, sentence, key = pending[0]
                        reference = self._reference_at(start)
                        self._attempted.add(key)
                        break
                    self._condition.wait(max(.01, delay) if pending else None)
            revised = sentence
            suggested = False
            began = time.monotonic()
            try:
                # One bounded sentence. Long/unpunctuated text stays untouched.
                if 3 <= len(sentence.strip()) <= 240:
                    candidate = self.engine.correct(sentence, context=reference,
                                                    protected=self.protected, cancel=self.cancelled,
                                                    vocabulary=self.vocabulary)
                    suggested = isinstance(candidate, str) and candidate.strip() != sentence.strip()
                    revised = safe_revision(sentence, candidate, self.protected, allow_punctuation=True)
            except Exception:
                self.metrics["errors"] += 1
            with self._condition:
                if self._closed:
                    return
                current_keys = {self._key(self._raw, s, v) for s, v in self._sentences()}
                if key not in current_keys:
                    continue
                self._revisions[key] = revised
                self.metrics["sentences_checked"] += 1
                self.metrics["sentences_changed"] += revised != sentence
                self.metrics["max_context_chars"] = max(self.metrics["max_context_chars"], len(reference))
                self.metrics["suggestions"] += suggested
                self.metrics["suggestions_rejected"] += suggested and revised == sentence
                self.metrics["max_seconds"] = max(self.metrics["max_seconds"], time.monotonic()-began)
                # Signal only; no external input/paste happens in this worker.
                self.emit()

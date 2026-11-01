"""Bounded, RAM-only references for one verified editable field."""
from __future__ import annotations

import hashlib
import time


def _identity(target):
    try:
        hwnd, pid, runtime = target.hwnd, target.pid, target.runtime_id
        if (type(hwnd) is not int or hwnd <= 0 or type(pid) is not int or pid <= 0
                or not isinstance(runtime, (tuple, list)) or not runtime
                or not all(type(value) is int for value in runtime)):
            return None
        return hwnd, pid, tuple(runtime)
    except AttributeError:
        return None


def same_field(left, right):
    identity = _identity(left)
    return identity is not None and identity == _identity(right)


class ShortContext:
    def __init__(self, *, clock=time.monotonic, ttl=180):
        self.clock, self.ttl = clock, ttl
        self.clear()

    def clear(self):
        self._key = self._fingerprint = None
        self._recent = ""
        self._topic = ""
        self._topic_at = 0.0
        self._at = 0.0
        self.source = "none"

    @staticmethod
    def _snapshot(target):
        if getattr(target, "paste_mode", "verified") != "verified":
            return None
        key = _identity(target)
        try:
            value, prefix, suffix = target.value, target.prefix, target.suffix
        except AttributeError:
            return None
        if key is None or not all(isinstance(part, str) for part in (value, prefix, suffix)):
            return None
        value, prefix, suffix = (part.replace("\r\n", "\n").replace("\r", "\n")
                                 for part in (value, prefix, suffix))
        if len(prefix) + len(suffix) > len(value) or not value.startswith(prefix) or not value.endswith(suffix):
            return None
        # Do not hash an unbounded document on the recording-start path.
        fingerprint = None
        if len(value) <= 100_000:
            fingerprint = (hashlib.sha256(value.encode("utf-8")).digest(), len(prefix), len(suffix))
        return key, fingerprint, value, prefix, suffix

    @staticmethod
    def _around(prefix, suffix, budget):
        if not prefix:
            return suffix[:budget].strip()
        if not suffix:
            return prefix[-budget:].strip()
        after = min(len(suffix), budget // 4)
        return (prefix[-(budget-after):] + "\n" + suffix[:after])[:budget].strip()

    def context_for(self, target):
        snapshot = self._snapshot(target)
        if snapshot is None or not snapshot[2].strip():
            self.clear()
            return ""
        key, fingerprint, _, prefix, suffix = snapshot
        if not prefix and not suffix:
            self.clear()
            return ""
        elapsed = self.clock() - self._at
        valid = (fingerprint is not None and key == self._key and fingerprint == self._fingerprint
                 and 0 <= elapsed <= self.ttl)
        recent = self._recent if valid else ""
        if not valid:
            self.clear()
        nearby = self._around(prefix, suffix, 240)
        topic_age = self.clock() - self._topic_at
        if not 0 <= topic_age <= self.ttl:
            self._topic = ""
        if self._topic and self._topic not in nearby and self._topic not in recent:
            references = [self._topic]
            if recent and recent not in nearby:
                references.append(recent)
            budget = 240 - sum(map(len, references)) - len(references)
            self.source = "field_and_recent"
            return "\n".join(references + [self._around(prefix, suffix, budget)]).strip()[:240]
        if recent and recent not in nearby:
            self.source = "field_and_recent"
            return (recent[:80] + "\n" + self._around(prefix, suffix, 159)).strip()[:240]
        self.source = "field" if nearby else "none"
        return nearby

    def remember(self, target, text, *, topics=()):
        snapshot = self._snapshot(target)
        if (snapshot is None or snapshot[1] is None or not snapshot[2].strip()
                or not isinstance(text, str) or not text.strip()):
            self.clear()
            return
        now = self.clock()
        topic = "、".join(topics)[:80]
        if topic:
            self._topic, self._topic_at = topic, now
        elif snapshot[0] != self._key or not 0 <= now - self._topic_at <= self.ttl:
            self._topic = ""
        self._key, self._fingerprint = snapshot[:2]
        self._recent, self._at = text.strip()[-80:], now

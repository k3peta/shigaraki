"""Read-only, bounded reverse paging of the local recognition JSONL file."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

BLOCK_BYTES = 64 * 1024
PAGE_BYTES = 512 * 1024
MAX_RECORD_BYTES = 256 * 1024
PAGE_RECORDS = 40
PAGE_LINES = 2000
MAX_VISIBLE_RECORDS = 200


class HistoryChanged(OSError):
    """The snapshot was replaced, truncated, or a selected record changed."""


@dataclass(frozen=True)
class Entry:
    start: int
    end: int
    digest: str
    at: str
    app: str
    outcome: str
    preview: str


@dataclass(frozen=True)
class Page:
    entries: tuple[Entry, ...]
    has_more: bool
    skipped: int
    bytes_read: int


def _record(raw):
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        raise ValueError("invalid history record")
    if "raw_text" in value and not isinstance(value["raw_text"], str):
        raise ValueError("invalid original text")
    for key in ("text", "raw_text", "at", "app", "outcome"):
        if isinstance(value.get(key), str):
            value[key].encode("utf-8")  # Reject escaped, unpaired surrogates before Qt sees them.
    return value


def _label(value, limit):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


class HistoryReader:
    """A snapshot's byte cursor; no file access until the first page request.

    Pages store offsets and short previews, not full transcripts. New appends
    are deliberately picked up on refresh; a selected record is hash checked.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.snapshot = None
        self.cursor = 0
        self.buffer = b""
        self.discarding = False

    def _check(self, stream):
        import os
        state = os.fstat(stream.fileno())
        identity = (state.st_dev, state.st_ino)
        if self.snapshot is None:
            self.snapshot = (identity, state.st_size, state.st_mtime_ns)
            self.cursor = state.st_size
        else:
            old_identity, size, modified = self.snapshot
            if (identity != old_identity or state.st_size < size or
                    state.st_size == size and state.st_mtime_ns != modified):
                raise HistoryChanged("history changed; refresh required")

    def page(self) -> Page:
        entries, skipped, used, lines = [], 0, 0, 0
        try:
            stream = self.path.open("rb")
        except FileNotFoundError:
            if self.snapshot is not None:
                raise HistoryChanged("history removed") from None
            return Page((), False, 0, 0)
        with stream:
            self._check(stream)
            while len(entries) < PAGE_RECORDS and lines < PAGE_LINES:
                split = self.buffer.rfind(b"\n")
                if split >= 0 or self.cursor == 0:
                    start = self.cursor + split + 1
                    end = self.cursor + len(self.buffer)
                    raw = self.buffer[split + 1:]
                    self.buffer = self.buffer[:split] if split >= 0 else b""
                    lines += 1
                    if self.discarding:
                        self.discarding = False
                    elif raw.strip():
                        try:
                            if len(raw) > MAX_RECORD_BYTES:
                                raise ValueError("oversized history record")
                            value = _record(raw)
                            entries.append(Entry(start, end, hashlib.sha256(raw).hexdigest(),
                                                 _label(value.get("at"), 80),
                                                 _label(value.get("app"), 140),
                                                 _label(value.get("outcome"), 60),
                                                 _label(value["text"], 140)))
                        except (ValueError, UnicodeError, RecursionError):
                            skipped += 1
                    if not self.buffer and self.cursor == 0:
                        break
                    continue
                if used >= PAGE_BYTES:
                    break
                length = min(BLOCK_BYTES, self.cursor, PAGE_BYTES - used)
                self.cursor -= length
                stream.seek(self.cursor)
                block = stream.read(length)
                if len(block) != length:
                    raise HistoryChanged("history truncated during read")
                used += length
                self.buffer = block + self.buffer
                if b"\n" not in self.buffer and len(self.buffer) > MAX_RECORD_BYTES:
                    self.buffer = b""
                    if not self.discarding:
                        skipped += 1
                    self.discarding = True
            self._check(stream)
        return Page(tuple(entries), bool(self.cursor or self.buffer), skipped, used)

    def detail(self, entry: Entry) -> tuple[str, str]:
        if not 0 < entry.end - entry.start <= MAX_RECORD_BYTES:
            raise HistoryChanged("invalid record bounds")
        try:
            stream = self.path.open("rb")
        except FileNotFoundError:
            raise HistoryChanged("history removed") from None
        with stream:
            self._check(stream)
            stream.seek(entry.start)
            raw = stream.read(entry.end - entry.start)
            if hashlib.sha256(raw).hexdigest() != entry.digest:
                raise HistoryChanged("selected history changed")
            value = _record(raw)
            self._check(stream)
        return value["text"], value.get("raw_text", value["text"])

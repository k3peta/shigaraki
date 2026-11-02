"""Bounded metadata-only paste history, independent from recognition logs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import PureWindowsPath
import re

MAX_READ_BYTES = 32 * 1024
MAX_ATTEMPTS = 8
OUTCOMES = {"confirmed", "sentUnverified", "notAttempted", "sendFailed", "unconfirmed"}
FAILURES = {"notAttempted", "sendFailed", "unconfirmed"}
REASONS = {"unavailable", "not_checked", "foreground", "focused_element", "protected_or_unfocused",
           "clipboard_editable", "clipboard_identity", "editable_document", "selection", "captured",
           "captured_clipboard_value", "captured_clipboard_text", "captured_clipboard_legacy",
           "foreground_changed", "modifier_down", "field_changed", "focus_changed", "ready"}
ENUMS = {"outcome": OUTCOMES, "edition": {"standard", "haas"},
         "target_capture": REASONS, "capture_fallback_from": REASONS | {""},
         "prepare_check": REASONS, "readback": {"matched", "unavailable_or_formatted", "unchanged", "not_checked"},
         "transfer_mode": {"verified", "clipboard", "none"},
         "reference_source": {"none", "field", "field_and_recent", "field_and_pages"}}
FLAGS = {"target_present", "asr_ready", "proof_ready", "punctuation_ready"}


def _basename(value):
    if not isinstance(value, str) or len(value) > 1024 or "://" in value:
        return "unavailable"
    name = PureWindowsPath(value).name
    if not re.fullmatch(r"[^\\/:\x00-\x1f]{1,96}\.exe", name, re.IGNORECASE):
        return "unavailable"
    return name


def _entry(value):
    if (not isinstance(value, dict) or not isinstance(value.get("outcome"), str)
            or value["outcome"] not in OUTCOMES):
        return None
    result = {}
    for key, allowed in ENUMS.items():
        item = value.get(key)
        if isinstance(item, str) and item in allowed:
            result[key] = item
    for key in FLAGS:
        if type(value.get(key)) is bool:
            result[key] = value[key]
    attempt = value.get("attempt_in_run")
    if type(attempt) is int and 1 <= attempt <= 1_000_000:
        result["attempt_in_run"] = attempt
    elapsed = value.get("seconds_after_preparation")
    if type(elapsed) in (int, float) and 0 <= elapsed <= 7 * 86400:
        result["seconds_after_preparation"] = elapsed
    version = value.get("version")
    if isinstance(version, str) and re.fullmatch(r"\d{1,3}\.\d{1,3}\.\d{1,3}(?:[a-z]\d{1,5})?", version):
        result["version"] = version
    timestamp = value.get("timestamp")
    result["timestamp"] = (timestamp if isinstance(timestamp, str) and
        re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:\+00:00|Z)", timestamp) else None)
    result["target_app"] = _basename(value.get("target_app"))
    return result


def _read_previous(path):
    try:
        if path.is_symlink():
            return {}
        with path.open("rb") as source:
            raw = source.read(MAX_READ_BYTES + 1)
        if len(raw) > MAX_READ_BYTES:
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def with_history(path, latest, *, edition, target_app="", timestamp=None):
    """Preserve latest fields while retaining eight safe attempts and one failure.

    Missing times from legacy diagnostics stay null; no past time is invented.
    Unverified dispatch is kept in attempts, not mislabeled as a failed paste.
    """
    previous = _read_previous(path)
    attempts = previous.get("recent_attempts")
    if isinstance(attempts, list):
        history = [entry for value in attempts[-MAX_ATTEMPTS:] if (entry := _entry(value)) is not None]
    else:
        legacy = _entry(previous)
        history = [legacy] if legacy else []
    failure = _entry(previous.get("latest_failure"))
    if not failure or failure["outcome"] not in FAILURES:
        failure = next((entry for entry in reversed(history) if entry["outcome"] in FAILURES), None)
    now = timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = dict(latest)
    result.update(timestamp=now, edition=edition, target_app=_basename(target_app))
    current = _entry(result)
    if current is not None:
        history.append(current)
        if current["outcome"] in FAILURES:
            failure = current
    result["recent_attempts"] = history[-MAX_ATTEMPTS:]
    result["latest_failure"] = failure
    return result

"""Optional, bounded input hints. Matching never guesses a website from its title."""
from __future__ import annotations

from pathlib import PureWindowsPath
import re
import unicodedata
from urllib.parse import urlsplit

MAX_PROFILES = 64
MAX_TOPIC = 400
MAX_TERMS = 32
MAX_TERM = 80


def normalize_host(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ""
    value = value.strip()
    if any(c.isspace() or ord(c) < 32 or c in "\\" for c in value):
        return ""
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme.lower() not in ("http", "https") or parsed.username is not None or parsed.password is not None:
            return ""
        _ = parsed.port  # Reject malformed/out-of-range ports rather than hiding them.
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            return ""
        return host
    except (ValueError, UnicodeError):
        return ""


def normalize_app(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ""
    name = PureWindowsPath(value.strip()).name.casefold()
    if not name.endswith(".exe") or len(name) > 200 or any(unicodedata.category(c).startswith("C") or c in '"<>|:*?' for c in name):
        return ""
    return name


def validate_profile(value):
    if not isinstance(value, dict) or value.get("kind") not in ("application", "website"):
        raise ValueError("kind")
    kind = value["kind"]
    identifier = (normalize_app if kind == "application" else normalize_host)(value.get("identifier"))
    if not identifier:
        raise ValueError("identifier")
    topic = value.get("topic", "")
    if not isinstance(topic, str) or len(topic) > MAX_TOPIC or any(unicodedata.category(c) in ("Cs", "Cc") and c not in "\n\t" for c in topic):
        raise ValueError("topic")
    terms = value.get("terms", [])
    if not isinstance(terms, list) or len(terms) > MAX_TERMS:
        raise ValueError("terms")
    cleaned, seen = [], set()
    for term in terms:
        if not isinstance(term, str):
            raise ValueError("terms")
        term = term.strip()
        if not term or len(term) > MAX_TERM or any(unicodedata.category(c).startswith("C") or c in "=," for c in term):
            raise ValueError("terms")
        if term.casefold() not in seen:
            seen.add(term.casefold()); cleaned.append(term)
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        raise ValueError("enabled")
    return {"kind": kind, "identifier": identifier, "topic": topic.strip(), "terms": cleaned, "enabled": enabled}


def load_profiles(values):
    profiles, seen = [], set()
    if not isinstance(values, list):
        return profiles
    for value in values[:MAX_PROFILES]:
        try:
            profile = validate_profile(value)
        except ValueError:
            continue
        key = profile["kind"], profile["identifier"]
        if key not in seen:
            profiles.append(profile); seen.add(key)
    return profiles


def resolve_profiles(values, app, host="", *, blocked=False):
    if blocked:
        return "", ()
    app, host = normalize_app(app), normalize_host(host)
    profiles = load_profiles(values)
    application = next((p for p in profiles if p["enabled"] and p["kind"] == "application" and p["identifier"] == app), None)
    website = next((p for p in profiles if p["enabled"] and p["kind"] == "website" and host and p["identifier"] == host), None)
    notes = "\n".join(p["topic"] for p in (application, website) if p and p["topic"])
    terms, seen = [], set()
    for profile in (website, application):
        for term in profile["terms"] if profile else ():
            if term.casefold() not in seen:
                terms.append(term); seen.add(term.casefold())
    return notes[:MAX_TOPIC * 2], tuple(terms)


def recognition_vocabulary(rules, preferred):
    """Identity entries reach Haas without adding new replacement spellings."""
    result = list(rules)
    known = {written for _, written in result}
    for term in preferred:
        if term not in known:
            result.append((term, term)); known.add(term)
    return result


def prioritize_hints(hints, preferred):
    # The resident worker takes preferred phrases from the end of the sequence.
    preferred = list(dict.fromkeys(preferred))[:64]
    tail = list(reversed(preferred))
    room = 64 - len(tail)
    remaining = [h for h in hints if h not in preferred]
    return tuple(remaining[-room:] if room else []) + tuple(tail)

"""Common AI vocabulary; saved user mappings take precedence without migration."""
from __future__ import annotations
import re
import unicodedata


_TO_HIRAGANA = str.maketrans({c: c - 0x60 for c in range(0x30A1, 0x30F7)})
_TO_KATAKANA = str.maketrans({c: c + 0x60 for c in range(0x3041, 0x3097)})
_TO_HALF_KANA = {ord(unicodedata.normalize("NFKC", chr(c))): chr(c)
                 for c in range(0xFF66, 0xFF9E)}
_TO_HALF_KANA.update({0x3099: "ﾞ", 0x309A: "ﾟ"})


def _kana_spellings(reading):
    """Equivalent kana only; leave unrelated characters and spaces intact."""
    full = re.sub("[ｦ-ﾟ]+", lambda m: unicodedata.normalize("NFKC", m[0]), reading)
    hiragana = full.translate(_TO_HIRAGANA)
    katakana = hiragana.translate(_TO_KATAKANA)
    half = "".join(unicodedata.normalize("NFD", c).translate(_TO_HALF_KANA)
                   if "ァ" <= c <= "ヺ" or c == "ー" else c for c in katakana)
    return tuple(dict.fromkeys((reading, hiragana, katakana, half)))

# Complete product names only; never replace fragments such as AI or GPT.
# The first reading is also used in the dictionary display.
AI_WORDS = (
    ("OpenAI", ("おーぷんえーあい", "おーぷんAI")),
    ("ChatGPT", ("ちゃっとじーぴーてぃー", "ちゃっとGPT")),
    ("Codex", ("こーでっくす",)),
    ("Antigravity", ("あんちぐらびてぃ", "あんちぐらびてぃー")),
    ("Claude", ("くろーど",)),
    ("Gemini", ("じぇみに",)),
)


def effective_words(user_words):
    custom = {heard: written for heard, written in user_words if heard and written}
    mappings = {}
    for written, readings in AI_WORDS:
        for reading in readings:
            for heard in _kana_spellings(reading):
                mappings[heard] = custom.get(written, written)
    for heard, written in custom.items():
        for alias in _kana_spellings(heard):
            mappings[alias] = written
    # Explicit entries win even when another entry generates the same alias.
    # Keep them last so the bounded ASR hints still prioritize user vocabulary.
    for heard in custom:
        mappings.pop(heard, None)
    mappings.update(custom)
    return list(mappings.items())


def _valid_term(word):
    return (isinstance(word, str) and bool(word.strip()) and len(word) <= 80
            and not any(unicodedata.category(c) == "Cc" for c in word))


def _mentioned(text, word):
    if not _valid_term(word) or len(word) < 2:
        return False
    escaped = re.escape(word)
    if re.fullmatch(r"[A-Za-z0-9 ]+", word):
        escaped = r"(?<![A-Za-z0-9_])" + escaped + r"(?![A-Za-z0-9_])"
        return re.search(escaped, text, re.IGNORECASE) is not None
    return word.casefold() in text.casefold()


def correction_glossary(rules, text, context=""):
    """Only dictionary entries evidenced by this utterance or its bounded reference."""
    grouped = {}
    for heard, written in rules:
        if _valid_term(written):
            aliases = grouped.setdefault(written, [])
            if _valid_term(heard) and heard != written and heard not in aliases:
                aliases.append(heard)
    def relevant(written, aliases, source):
        return any(_mentioned(source, term) for term in (written, *aliases))
    ranked = sorted(grouped.items(), key=lambda item:
        0 if relevant(*item, text) else 1 if relevant(*item, context) else 2)
    result, characters = [], 0
    for written, aliases in ranked:
        if not relevant(written, aliases, text) and not relevant(written, aliases, context):
            continue
        # Prefer short readings so kana variants cannot crowd out other names.
        readings = sorted(aliases, key=len)[:3]
        cost = len(written) + sum(map(len, readings))
        if characters + cost > 600:
            continue
        result.append({"written": written, "readings": readings})
        characters += cost
        if len(result) == 16:
            break
    # A mention of 相模女子大学 also contains 相模女子大. Give the model the
    # complete registered name rather than competing fragments of that name.
    return [entry for entry in result if not any(entry["written"] != other["written"]
        and entry["written"] in other["written"] for other in result)]


def topic_terms(text, rules):
    """Explicit dictionary spelling only; never extract arbitrary names/history."""
    terms = sorted({written for _, written in rules if _mentioned(text, written)}, key=lambda t: (-len(t), t))
    chosen = []
    for term in terms:
        if any(term in existing for existing in chosen) or len("、".join(chosen + [term])) > 80:
            continue
        chosen.append(term)
        if len(chosen) == 8:
            break
    return tuple(chosen)


def recognition_terms(rules, user_words=(), *, context=""):
    """Prefer written terms, then use spare hint slots for registered kana."""
    if context:
        # The end of this list has priority in the existing bounded selector.
        def relevant(rule):
            return any(_mentioned(context, term) for term in rule)
        rules = [rule for rule in rules if not relevant(rule)] + [rule for rule in rules if relevant(rule)]
    terms = []
    for _, written in reversed(rules):
        if _valid_term(written) and written not in terms:
            terms.append(written)
        if len(terms) == 64:
            break
    readings = []
    for heard, written in reversed(user_words):
        if not heard or not written:
            continue
        for spelling in _kana_spellings(heard):
            if (len(terms) + len(readings) < 64 and len(spelling) <= 80
                    and re.fullmatch("[ぁ-ゖァ-ヶー・]+", spelling)
                    and spelling not in terms and spelling not in readings):
                readings.append(spelling)
    return tuple(reversed(readings)) + tuple(reversed(terms))


def display_words(user_words):
    mappings = dict(effective_words(user_words))
    rows = []
    for _, readings in AI_WORDS:
        alias = readings[0].translate(_TO_KATAKANA)
        rows.append((alias, mappings[alias]))
    return rows + [(heard, written) for heard, written in user_words if heard and written]

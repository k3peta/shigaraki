"""Short-lived foreground page titles; no history files, document text or I/O."""
import time
import unicodedata

BROWSERS = frozenset({"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"})


def excluded(title):
    text = title.casefold()
    return any(word in text for word in ("incognito", "inprivate", "private browsing", "private window",
                                        "シークレット", "プライベート", "password", "パスワード"))


def clean_title(title):
    return "".join(char for char in title if not unicodedata.category(char).startswith("C"))[:160].strip()


class PageContext:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.clear()

    def clear(self):
        self.pages = []

    def observe(self, title, *, blocked=False):
        if blocked or excluded(title):
            self.clear()
            return
        title, now = clean_title(title), self.clock()
        if title:
            self.pages = [(title, now)] + [(text, at) for text, at in self.pages
                                          if text != title and 0 <= now-at <= 180][:2]

    def reference(self):
        now = self.clock()
        self.pages = [(text, at) for text, at in self.pages if 0 <= now-at <= 180]
        return "\n".join(text for text, _ in self.pages)[:240]


def combine_reference(nearby, title, pages):
    # Current editable field has priority over ambient titles when budgets collide.
    if excluded(title):
        return ""
    current = clean_title(title)
    ambient = "\n".join(part for part in (current, pages) if part)
    if not ambient:
        return nearby[:240]
    field = nearby if len(nearby) <= 160 else nearby[:80] + nearby[-80:]
    room = 240 - len(field) - (1 if field else 0)
    return (ambient[:room] + ("\n" + field if field else ""))[:240]

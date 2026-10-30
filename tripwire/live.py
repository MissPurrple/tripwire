"""Live watching: checking one page every few minutes while someone has the
Live watch page open (a news live page, say). Nothing is stored; the browser
keeps the last version and sends it back with each check."""

import re

from . import changes, sources

MAX_TEXT = 200_000           # characters kept from a page (a long live page is well under this)
MIN_SECONDS_BETWEEN = 45     # be gentle with the websites being watched

# Bits of a page that change every time you look, without anything new having happened.
_NOISE = [
    re.compile(r"\b(?:\d+|an?|one)\s*(?:seconds?|secs?|minutes?|mins?|m|hours?|hrs?|h|days?|d)\s+ago\b", re.I),
    re.compile(r"\b(?:just now|moments? ago|updated\s+(?:just now|\d+\s*\w+\s+ago))\b", re.I),
    re.compile(r"\b\d[\d,.]*\s*(?:people\s+)?(?:watching|viewers|readers)\s+(?:now|live)\b", re.I),
]


def steady_text(text, limit=MAX_TEXT):
    """The page's text without the bits that change on every visit (cut to `limit` characters, if given)."""
    for pattern in _NOISE:
        text = pattern.sub("", text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:limit] if limit else text


def check(url, previous=None, browser=None):
    """Fetch the page now and compare it with `previous` (the text from the last
    check). `browser` is what the first check decided (read like a browser or not),
    sent back each time so every check reads the page the same way.
    Returns {"text", "changed", "hunks", "added", "removed", "browser"} or
    {"gone": True} if the page no longer exists."""
    page = sources.fetch_page(url, browser=browser)
    if page["status"] == "removed":
        return {"gone": True, "changed": previous is not None}
    text = steady_text(page["text"])
    used = page.get("browser", False)
    if previous is None or text == previous:
        return {"text": text, "changed": False, "hunks": [], "added": 0, "removed": 0, "browser": used}
    result = changes.compare(previous, text)
    return {"text": text, "changed": bool(result["hunks"]), "hunks": result["hunks"][:30],
            "added": result["words_added"], "removed": result["words_removed"], "browser": used}

"""Compare two versions of a text and describe what changed.

This is the "brain" of Tripwire. Given the text before and the text after, it
works out:
  - which words were added and removed,
  - how big the change is (punctuation only, tiny, an edit, major, a large removal),
  - whether any capitalised words (often names) changed spelling, because a
    name being spelled differently can mean a language is spreading or fading.

It never talks to the internet, so it is easy to test.
"""

import difflib
import re

# Splits text into words and punctuation marks, e.g. "Hi, Goa!" -> ["Hi", ",", "Goa", "!"]
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Size thresholds, counted in changed words (added + removed).
TINY_LIMIT = 5
MAJOR_LIMIT = 100

# How alike two words must be to count as "the same word, spelled differently" (0 to 1).
SPELLING_SIMILARITY = 0.6

# How many words of surrounding text to show around each change.
CONTEXT_WORDS = 8

# Human-readable labels for each kind of change, in order of size.
CATEGORIES = {
    "formatting": "Punctuation or formatting only",
    "tiny": "Tiny: spelling or wording",
    "edit": "Edit",
    "major": "Major change",
    "removal": "Large removal",
    "page_removed": "Page removed",
}


def tokenize(text):
    return TOKEN_RE.findall(text or "")


def is_word(token):
    return bool(re.match(r"\w", token))


def join_tokens(tokens):
    """Glue tokens back into readable text (no space before punctuation)."""
    out = ""
    for token in tokens:
        if out and is_word(token):
            out += " "
        out += token
    return out


def looks_like_name(word):
    return word[:1].isupper()


def similar(a, b):
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def compare(before, after):
    """Compare two texts. Returns a dict describing the change.

    Keys:
      category          one of CATEGORIES
      words_added       number of words added
      words_removed     number of words removed
      spelling_changes  list of distinct (old, new) single-word swaps that look like respellings
      name_changes      the subset of spelling_changes where the old word is capitalised
      hunks             list of {before, removed, added, after} snippets for display
    """
    old = tokenize(before)
    new = tokenize(after)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)

    words_added = 0
    words_removed = 0
    spelling_changes = []
    other_change = False
    hunks = []

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        removed = old[i1:i2]
        added = new[j1:j2]
        removed_words = [t for t in removed if is_word(t)]
        added_words = [t for t in added if is_word(t)]
        words_removed += len(removed_words)
        words_added += len(added_words)

        if (len(removed_words) == 1 and len(added_words) == 1
                and similar(removed_words[0], added_words[0]) >= SPELLING_SIMILARITY):
            spelling_changes.append((removed_words[0], added_words[0]))
        elif removed_words or added_words:
            other_change = True

        hunks.append({
            "before": join_tokens(old[max(0, i1 - CONTEXT_WORDS):i1]),
            "removed": join_tokens(removed),
            "added": join_tokens(added),
            "after": join_tokens(old[i2:i2 + CONTEXT_WORDS]),
        })

    changed = words_added + words_removed
    if changed == 0:
        category = "formatting"
    elif not other_change or changed <= TINY_LIMIT:
        category = "tiny"
    elif words_removed >= MAJOR_LIMIT and words_removed > 3 * words_added:
        category = "removal"
    elif changed > MAJOR_LIMIT:
        category = "major"
    else:
        category = "edit"

    spelling_changes = list(dict.fromkeys(spelling_changes))  # each respelling listed once
    return {
        "category": category,
        "words_added": words_added,
        "words_removed": words_removed,
        "spelling_changes": spelling_changes,
        "name_changes": [pair for pair in spelling_changes if looks_like_name(pair[0])],
        "hunks": hunks,
    }


def describe(result):
    """One-line plain-English summary of a compare() result."""
    parts = [CATEGORIES[result["category"]]]
    if result["category"] != "formatting":
        parts.append(f"{result['words_added']} words added, {result['words_removed']} removed")
    if result["name_changes"]:
        names = ", ".join(f"{old} → {new}" for old, new in result["name_changes"][:3])
        parts.append(f"possible name spelling change: {names}")
    return " · ".join(parts)

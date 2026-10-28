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

# A "word swap" is a short phrase replaced by another short phrase,
# e.g. "killed" -> "unalive". Longer rewrites aren't counted as swaps.
SWAP_MAX_WORDS = 4

# Human-readable labels for each kind of change, in order of size.
CATEGORIES = {
    "formatting": "Punctuation or formatting only",
    "tiny": "Tiny: spelling or wording",
    "edit": "Edit",
    "major": "Major change",
    "removal": "Large removal",
    "page_removed": "Page removed",
    "video_retitled": "Video retitled",
    "video_restricted": "Video restricted",
    "video_gone": "Video unavailable",
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
    replacements = []
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

        if 0 < len(removed_words) <= SWAP_MAX_WORDS and 0 < len(added_words) <= SWAP_MAX_WORDS:
            replacements.append((" ".join(removed_words), " ".join(added_words)))

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
        "replacements": replacements,
        "hunks": hunks,
    }


# Where one sentence ends and the next begins: after . ! or ? (optionally
# followed by a closing quote or bracket), then whitespace.
SENTENCE_END_RE = re.compile(r"(?:(?<=[.!?])|(?<=[.!?][\"'”’)\]]))\s+")

# Above this many words, a changed stretch is reported as "rewritten" rather
# than compared word by word (keeps very different texts fast to compare).
BLOCK_LIMIT = 4000


def sentences(text):
    """Split text into sentences, ignoring where lines and pages happen to break."""
    text = re.sub(r"-[ \t]*\n[ \t]*(?=[a-z])", "", text or "")  # re-join words hyphenated across lines
    out = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        out.extend(s for s in SENTENCE_END_RE.split(paragraph) if s)
    return out


def compare_long(before, after):
    """compare() for book-length texts. First lines up the two texts sentence
    by sentence (fast, and ignores different page and line breaks), then
    compares only the sentences that differ, word by word."""
    old = sentences(before)
    new = sentences(after)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    total = {"words_added": 0, "words_removed": 0, "spelling_changes": [], "replacements": [], "hunks": []}
    rewritten = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        old_block, new_block = " ".join(old[i1:i2]), " ".join(new[j1:j2])
        if len(old_block.split()) + len(new_block.split()) > BLOCK_LIMIT:
            removed = sum(1 for t in tokenize(old_block) if is_word(t))
            added = sum(1 for t in tokenize(new_block) if is_word(t))
            total["words_removed"] += removed
            total["words_added"] += added
            rewritten += 1
            total["hunks"].append({"before": " ".join(old[i1 - 1].split()[-CONTEXT_WORDS:]) if i1 else "",
                                   "removed": f"[{removed} words]" if removed else "",
                                   "added": f"[{added} words]" if added else "",
                                   "after": " ".join(old[i2].split()[:CONTEXT_WORDS]) if i2 < len(old) else ""})
            continue
        part = compare(old_block, new_block)
        for key in ("words_added", "words_removed"):
            total[key] += part[key]
        for key in ("spelling_changes", "replacements", "hunks"):
            total[key].extend(part[key])

    changed = total["words_added"] + total["words_removed"]
    if not total["hunks"]:
        category = "formatting"
    elif changed <= TINY_LIMIT:
        category = "tiny"
    elif total["words_removed"] >= MAJOR_LIMIT and total["words_removed"] > 3 * total["words_added"]:
        category = "removal"
    elif changed > MAJOR_LIMIT:
        category = "major"
    else:
        category = "edit"
    spelling = list(dict.fromkeys(total["spelling_changes"]))
    return dict(total, category=category, spelling_changes=spelling,
                name_changes=[p for p in spelling if looks_like_name(p[0])],
                rewritten_blocks=rewritten, sentences_before=len(old), sentences_after=len(new))


def swap_counts(replacements, limit=100):
    """The most common word swaps, as [(old, new, how many times)]."""
    counts = {}
    for pair in replacements:
        counts[pair] = counts.get(pair, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(old, new, n) for (old, new), n in ranked[:limit]]


def describe(result):
    """One-line plain-English summary of a compare() result."""
    parts = [CATEGORIES[result["category"]]]
    if result["category"] != "formatting":
        parts.append(f"{result['words_added']} words added, {result['words_removed']} removed")
    if result["name_changes"]:
        names = ", ".join(f"{old} → {new}" for old, new in result["name_changes"][:3])
        parts.append(f"possible name spelling change: {names}")
    return " · ".join(parts)

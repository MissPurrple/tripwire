"""Before copies: what a page said some time ago, from a copy nobody can quietly edit.
On the site these are the "Test against Kiwix" and "Test against Internet Archive" buttons.

  - Wikipedia articles: Kiwix (kiwix.org) publishes dated offline copies of
    every Wikipedia, which anyone can download and keep. Tripwire reads the
    article from the newest one through library.kiwix.org, and compares it with
    the article on Wikipedia today.
  - Other pages (or a Wikipedia that Kiwix doesn't have): the Internet
    Archive's copy from about a year ago.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin
from xml.etree import ElementTree

from . import changes, db, sources

KIWIX = "https://library.kiwix.org"
# Full articles. Kiwix's "mini" and "nodet" copies keep only the start of each
# article, which would look like huge removals.
KIWIX_FLAVOURS = ["maxi", "nopic"]
ARCHIVE_DAYS_AGO = 365
WIKIPEDIA_NAMES = {"en": "English", "hi": "Hindi", "st": "Sesotho", "zu": "Zulu", "xh": "Xhosa", "tn": "Setswana",
                   "fr": "French", "pt": "Portuguese", "sw": "Swahili", "af": "Afrikaans", "gom": "Konkani",
                   "mr": "Marathi", "ta": "Tamil", "bn": "Bengali", "es": "Spanish", "de": "German"}
NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/terms/"}
# mwoffliner (which makes Kiwix's Wikipedia copies) adds this licence line at the end of every article.
KIWIX_FOOTER = re.compile(r"\n[^\n]*(This article is issued from|Retrieved from) Wikipedia.*$", re.S | re.I)
# Words found on "prove you're not a robot" pages (Anubis, Cloudflare and the like), which some
# sites show to automated visitors such as Tripwire's server instead of the page itself.
CHALLENGE = re.compile(r"not a (?:ro)?bot|anubis|proof[- ]of[- ]work|checking your browser|just a moment|"
                       r"enable javascript and cookies|cf-challenge|challenge-platform|captcha|"
                       r"\bAI (?:companies|crawlers|scrapers)", re.I)
# An older copy this short, next to a page many times longer today, is almost certainly not the
# real page (an error or robot-check page): Tripwire refuses to compare it.
TOO_SHORT_WORDS = 150
TOO_SHORT_RATIO = 8
PREVIEW_WORDS = 80


class Blocked(sources.SourceError):
    """A site showed a robot check (or another page) instead of the copy Tripwire asked for."""


def page_title(html):
    """The page's own title and first heading, as plain text, lower case."""
    found = [re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I), re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)]
    return " | ".join(sources.html_to_text(m.group(1)).lower() for m in found if m)


def same_title(title, html):
    return title.replace("_", " ").strip().lower() in page_title(html).replace("_", " ")


def preview(text, words=PREVIEW_WORDS):
    parts = text.split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def kiwix_copy(lang):
    """The newest full Kiwix copy of the Wikipedia in language `lang` (e.g. "en", "st"), or None.
    Returns {"name", "flavour", "date", "content_url", "download"}."""
    # Kiwix names a copy "wikipedia_en_all" and says separately whether it's the full
    # ("maxi"), no-pictures ("nopic") or shortened kind; some older copies put it in the name.
    for name in (f"wikipedia_{lang}_all", *(f"wikipedia_{lang}_all_{f}" for f in KIWIX_FLAVOURS)):
        response = sources._get(KIWIX + "/catalog/v2/entries", params={"name": name, "count": 20})
        if response.status_code != 200:
            raise sources.SourceError(f"Kiwix's library answered with error {response.status_code}")
        full = [c for c in parse_catalog(response.content) if c["flavour"] in KIWIX_FLAVOURS]
        if full:
            return max(full, key=lambda c: (c["date"], -KIWIX_FLAVOURS.index(c["flavour"])))
    return None


def parse_catalog(xml):
    """Read Kiwix's catalogue (an OPDS/Atom feed) into a list of copies."""
    try:
        feed = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise sources.SourceError("Kiwix's library sent something Tripwire couldn't read.") from exc
    copies = []
    for entry in feed.findall("atom:entry", NS):
        content = download = None
        for link in entry.findall("atom:link", NS):
            if link.get("type") == "text/html":
                content = urljoin(KIWIX, link.get("href", ""))
            elif link.get("rel", "").endswith("acquisition/open-access"):
                download = re.sub(r"\.meta4$", "", link.get("href", ""))
        date = (entry.findtext("dc:issued", "", NS) or entry.findtext("atom:updated", "", NS))[:10]
        name = entry.findtext("atom:name", "", NS)
        flavour = entry.findtext("atom:flavour", "", NS) or next(
            (f for f in ("maxi", "nopic", "mini", "nodet") if f"_{f}" in (content or name)), "")
        if content:
            copies.append({"name": name, "flavour": flavour, "date": date,
                           "content_url": content.rstrip("/"), "download": download})
    return copies


def kiwix_fetch(copy, path, title):
    """Read the page at `path` in a Kiwix copy and check it really is the article called `title`.
    Uses Kiwix's "raw" address, which sends the page exactly as stored in the offline copy.
    Returns (text, link to read it, how it was fetched) or (None, None, None) if it isn't there.
    Raises Blocked if Kiwix's site sent a robot check instead."""
    path = path.lstrip("/")
    read_link = f"{copy['content_url']}/{path}"
    blocked = False
    for url in (f"{KIWIX}/raw/{kiwix_name(copy)}/content/{path}", read_link):
        response = sources._get(url)
        if response.status_code != 200 or "html" not in response.headers.get("Content-Type", "html"):
            continue
        html = response.text
        redirected = bool(getattr(response, "history", None))  # an article under its old name points to the new one
        if not (same_title(title, html) or (redirected and page_title(html))):
            blocked = blocked or bool(CHALLENGE.search(html))
            continue
        text = KIWIX_FOOTER.sub("", sources.html_to_text(html)).strip()
        if text:
            return text, read_link, {"fetched_from": response.url or url, "status": response.status_code,
                                     "page_title": page_title(html)[:200], "fetched_at": db.now()}
    if blocked:
        raise Blocked("Kiwix's online library showed Tripwire a robot check (a “prove you're not a bot” page) "
                      "instead of the article, so Tripwire couldn't read its copy.")
    return None, None, None


def kiwix_article(copy, title):
    """The text of one article in a Kiwix copy, the link to read it there, and how it was fetched.
    Returns (None, None, None) if the copy doesn't have it (for example, it's newer)."""
    path = quote(title.replace(" ", "_"), safe="()_,'!-.")
    for candidate in (f"A/{path}", path):  # older copies keep articles under A/
        found = kiwix_fetch(copy, candidate, title)
        if found[0]:
            return found
    return None, None, None


def archive_copy(url, days_ago=ARCHIVE_DAYS_AGO):
    """The Internet Archive's copy of `url` from about `days_ago` days ago, or None.
    Returns {"text", "date", "link"}."""
    moment = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y%m%d")
    response = sources._get("https://archive.org/wayback/available", params={"url": url, "timestamp": moment})
    if response.status_code != 200:
        return None
    closest = response.json().get("archived_snapshots", {}).get("closest")
    if not closest or not closest.get("available"):
        return None
    stamp = closest["timestamp"]
    # "id_" asks for the page exactly as it was saved, without the Archive's own toolbar.
    page = sources._get(f"https://web.archive.org/web/{stamp}id_/{url}", timeout=60)
    if page.status_code >= 400:
        return None
    if "pdf" in page.headers.get("Content-Type", "").lower() or page.content[:5] == b"%PDF-":
        text, title = sources.pdf_text(page.content), ""
    else:
        text, title = sources.html_to_text(page.text), page_title(page.text)
        if CHALLENGE.search(page.text) and len(text.split()) < 400:
            raise Blocked("The copy the Internet Archive saved is a robot check (a “prove you're not a bot” page), "
                          "not the page itself, so Tripwire can't compare it.")
    return {"text": text, "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "link": closest["url"],
            "how": {"fetched_from": f"https://web.archive.org/web/{stamp}id_/{url}", "status": page.status_code,
                    "page_title": title[:200], "fetched_at": db.now()}}


def month_name(date):
    try:
        return datetime.strptime(date[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        return date


class NotFound(sources.SourceError):
    """No copy of exactly this page. `suggestions` lists similar ones to try instead:
    [{"label", "kiwix_path" or "archive_url"}]."""

    def __init__(self, message, source, suggestions, on_date=None, blocked=False):
        super().__init__(message)
        self.source, self.suggestions, self.on_date, self.blocked = source, suggestions, on_date, blocked


def kiwix_name(copy):
    """The short name Kiwix uses for one copy, e.g. wikipedia_en_all_maxi_2024-01."""
    return copy["content_url"].rstrip("/").rsplit("/", 1)[-1]


def kiwix_page(copy, path):
    """The text of the page at `path` inside a Kiwix copy (chosen from Kiwix's suggestions)."""
    return kiwix_fetch(copy, path, path.rsplit("/", 1)[-1].replace("_", " "))


def kiwix_similar(copy, title, limit=6):
    """Articles in a Kiwix copy whose titles are like `title` (Kiwix's own title search)."""
    found = []
    for term in dict.fromkeys([title, title.split(" (")[0], title.split()[0]]):
        response = sources._get(KIWIX + "/suggest", params={"content": kiwix_name(copy), "term": term, "count": limit})
        try:
            rows = response.json() if response.status_code == 200 else []
        except ValueError:
            rows = []
        for row in rows:
            if row.get("kind") == "path" and row.get("path") and row["path"] not in {f["kiwix_path"] for f in found}:
                found.append({"label": row.get("value") or row["path"], "kiwix_path": row["path"]})
        if len(found) >= limit:
            break
    return found[:limit]


def archive_similar(url, limit=6):
    """Other addresses the Internet Archive has saved on the same site, near this one
    (the same folder, e.g. other notices in a gazette's folder)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    folder = parsed.path.rsplit("/", 1)[0] if parsed.path.count("/") > 1 else ""
    response = sources._get("https://web.archive.org/cdx/search/cdx",
                            params={"url": f"{parsed.netloc}{folder}/*", "output": "json", "fl": "original",
                                    "collapse": "urlkey", "filter": "statuscode:200", "limit": 50})
    try:
        rows = response.json()[1:] if response.status_code == 200 else []  # the first row is the column names
    except ValueError:
        rows = []
    seen, found = set(), []
    for (original,) in rows:
        key = original.split("://", 1)[-1].rstrip("/").lower()
        if key not in seen and key != url.split("://", 1)[-1].rstrip("/").lower():
            seen.add(key)
            found.append({"label": original, "archive_url": original})
    return found[:limit]


def compare_with_before(target, source="auto", kiwix_path=None, archive_url=None, on_date=None):
    """Compare a page today with an older copy of it. `target` is a Wikipedia
    link (or article title) or any web link.

    source: "kiwix" (Kiwix's offline Wikipedia), "archive" (the Internet Archive),
            "wikipedia_date" (Wikipedia's own saved version from `on_date`, e.g. the date of
            Kiwix's copy), or "auto" (Kiwix first for Wikipedia, then the Internet Archive).
    kiwix_path / archive_url: compare with this similar page instead (chosen from
            the suggestions of a NotFound).
    Raises NotFound (with similar pages to try) or sources.SourceError. Returns a
    dict ready to be saved as a before_check."""
    target = target.strip()
    if not target:
        raise sources.SourceError("Paste a link first.")
    found = None
    wikipedia = sources.is_wikipedia_link(target) or "://" not in target
    if wikipedia:
        lang, asked = sources.parse_wikipedia_target(target)
        wiki = sources.Wikipedia(lang)
        rev = wiki.revision_at(asked)
        title, url = rev["title"], wiki.article_url(rev["title"])
        today = wiki.text_of(rev["revid"])
        after_link = wiki.revision_url(rev["revid"])
        after_how = {"fetched_from": after_link, "status": 200, "page_title": title,
                     "fetched_at": db.now(), "note": f"Wikipedia's version {rev['revid']}, saved {rev['timestamp'][:10]}"}
    else:
        url = target if "://" in target else "https://" + target
        page = sources.fetch_page(url)
        if page["status"] == "removed":
            raise sources.SourceError("That page doesn't exist any more (it answered 'not found'). "
                                      "Try “Test against Internet Archive” to see if an older copy was saved.")
        title, today, after_link = url.split("://", 1)[1].rstrip("/"), page["text"], url
        after_how = {"fetched_from": url, "status": 200, "page_title": "", "fetched_at": db.now()}

    if source in ("kiwix", "wikipedia_date") and not wikipedia:
        raise sources.SourceError("Kiwix keeps offline copies of whole collections, like Wikipedia, not of single "
                                  "websites. For this page, use “Test against Internet Archive”.")
    if source == "wikipedia_date":
        try:
            moment = datetime.strptime((on_date or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise sources.SourceError("That date doesn't look right.") from exc
        old = wiki.revision_at(asked, days_ago=max(1, (datetime.now(timezone.utc) - moment).days))
        if old["timestamp"][:10] > on_date[:10]:
            raise sources.SourceError(f"The article didn't exist on Wikipedia yet on {on_date[:10]}.")
        found = {"source": "wikipedia", "text": wiki.text_of(old["revid"]), "before_date": old["timestamp"][:10],
                 "before_link": wiki.revision_url(old["revid"]), "download_link": None,
                 "source_name": f"Wikipedia's own saved version from {old['timestamp'][:10]} "
                                f"(as it was on {on_date[:10]}, the date of Kiwix's copy)",
                 "how": {"fetched_from": wiki.revision_url(old["revid"]), "status": 200, "page_title": old["title"],
                         "fetched_at": db.now(), "note": f"Wikipedia's version {old['revid']}"}}
    if wikipedia and source in ("kiwix", "auto"):
        copy = kiwix_copy(lang)
        if not copy and source == "kiwix":
            raise sources.SourceError(f"Kiwix doesn't have an offline copy of the {WIKIPEDIA_NAMES.get(lang, lang)} "
                                      "Wikipedia yet. Try “Test against Internet Archive”.")
        if copy:
            try:
                if kiwix_path:
                    text, link, how = kiwix_page(copy, kiwix_path)
                    compared_with = kiwix_path.rsplit("/", 1)[-1].replace("_", " ")
                else:
                    text, link, how = kiwix_article(copy, title)
                    if text is None and title != asked:
                        text, link, how = kiwix_article(copy, asked)  # the article may have had another name then
                    compared_with = None
            except Blocked as exc:
                if source != "kiwix":
                    text = None
                else:
                    raise NotFound(str(exc), "kiwix", [], on_date=copy["date"], blocked=True) from exc
            if text:
                where = f"Kiwix's offline {WIKIPEDIA_NAMES.get(lang, lang)} Wikipedia ({month_name(copy['date'])})"
                found = {"source": "kiwix", "text": text, "before_date": copy["date"], "before_link": link,
                         "download_link": copy["download"], "how": how,
                         "source_name": f"“{compared_with}” in {where}" if compared_with else where}
            elif source == "kiwix":
                raise NotFound(f"Kiwix's offline copy ({month_name(copy['date'])}) has no article called “{title}”. "
                               "It may be newer than the copy, or have had another name then.",
                               "kiwix", kiwix_similar(copy, title), on_date=copy["date"])
    if not found and source in ("archive", "auto"):
        old = archive_copy(archive_url or url)
        if old:
            other = f" of {archive_url}" if archive_url else ""
            found = {"source": "archive", "text": old["text"], "before_date": old["date"],
                     "before_link": old["link"], "download_link": None, "how": old["how"],
                     "source_name": f"the Internet Archive's copy{other} ({month_name(old['date'])})"}
        elif source == "archive":
            raise NotFound("The Internet Archive has no saved copy of this exact address.", "archive",
                           archive_similar(url))
    if not found:
        raise sources.SourceError("Tripwire couldn't find an older copy of that page, in Kiwix or the "
                                  "Internet Archive. You can keep a copy from today in My shelf.")

    words_before, words_after = len(found["text"].split()), len(today.split())
    if words_before < TOO_SHORT_WORDS and words_after >= TOO_SHORT_RATIO * max(words_before, 1):
        raise sources.SourceError(
            f"The older copy Tripwire received is only {words_before} words long, against {words_after:,} today, so it's "
            "almost certainly not the real page (more likely an error or a robot-check page). Tripwire won't compare it. "
            f"You can look at it yourself: {found['before_link']}")
    result = changes.compare_long(found["text"], today)
    swaps = changes.swap_counts(result["replacements"])
    if not result["hunks"]:
        summary = "No differences in the words since then."
    else:
        summary = changes.describe(result)
    details = {"swaps": swaps[:30], "hunks": result["hunks"][:200], "hunk_count": len(result["hunks"]),
               "kinds": changes.kind_counts(result["hunks"]), "name_changes": result["name_changes"],
               "words_before": words_before, "words_after": words_after, "after_link": after_link,
               "before_preview": preview(found["text"]), "after_preview": preview(today),
               "before_how": found["how"], "after_how": after_how}
    return {"url": url, "title": title, "source": found["source"], "source_name": found["source_name"],
            "before_date": found["before_date"], "before_link": found["before_link"],
            "download_link": found["download_link"], "category": result["category"],
            "summary": summary, "details": details}


# -- keeping the results --------------------------------------------------------

def save(conn, user_id, result):
    """Keep a comparison made by compare_with_before. Returns its id."""
    return conn.insert(
        """INSERT INTO before_checks (user_id, url, title, source, source_name, before_date, before_link,
               download_link, category, summary, details, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, result["url"], result["title"][:200], result["source"], result["source_name"],
         result["before_date"], result["before_link"], result["download_link"], result["category"],
         result["summary"], json.dumps(result["details"]), db.now()))


# -- locked with the person's vault ------------------------------------------------
# A result is first kept readable (Tripwire had to read both copies to compare them), then the
# person's browser locks it with their vault and Tripwire swaps the readable one for the locked one.
# After that, nothing says which page it was: only that a comparison was made, and when.

SEALED = re.compile(r"[A-Za-z0-9+/]+={0,2}")


def lock(conn, user_id, check_id, sealed_title, sealed):
    """True if a readable result was swapped for its locked version."""
    if not all(isinstance(v, str) and 16 <= len(v) <= limit and SEALED.fullmatch(v)
               for v, limit in ((sealed_title, 20_000), (sealed, 8_000_000))):
        return False
    found = conn.execute("SELECT id FROM before_checks WHERE id = ? AND user_id = ? AND locked = 0",
                         (check_id, user_id)).fetchone()
    if not found:
        return False
    conn.execute("""UPDATE before_checks SET url = '', title = 'Locked comparison', source = '', source_name = '',
                        before_date = NULL, before_link = NULL, download_link = NULL, category = '', summary = '',
                        details = '{}', locked = 1, sealed_title = ?, sealed = ?
                    WHERE id = ? AND user_id = ? AND locked = 0""", (sealed_title, sealed, check_id, user_id))
    return True


def locked(conn, user_id):
    """Locked results, for the browser to open and match to what's being watched."""
    return conn.execute("SELECT id, sealed_title, created_at FROM before_checks WHERE user_id = ? AND locked = 1 ORDER BY id",
                        (user_id,)).fetchall()


def unlocked_ids(conn, user_id):
    return [r["id"] for r in conn.execute("SELECT id FROM before_checks WHERE user_id = ? AND locked = 0",
                                          (user_id,)).fetchall()]


def checks(conn, user_id):
    return conn.execute("""SELECT id, url, title, source, source_name, before_date, category, summary, created_at
                           FROM before_checks WHERE user_id = ? ORDER BY id DESC""", (user_id,)).fetchall()


def own(conn, user_id, check_id):
    return conn.execute("SELECT * FROM before_checks WHERE id = ? AND user_id = ?", (check_id, user_id)).fetchone()

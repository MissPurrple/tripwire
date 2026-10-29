"""Before copies: what a page said some time ago, from a copy nobody can quietly edit.

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


def kiwix_article(copy, title):
    """The text of one article in a Kiwix copy, and the link to read it there.
    Returns (None, None) if the copy doesn't have it (for example, it's newer)."""
    path = quote(title.replace(" ", "_"), safe="()_,'!-.")
    for candidate in (f"A/{path}", path):  # older copies keep articles under A/
        response = sources._get(f"{copy['content_url']}/{candidate}")
        if response.status_code == 200 and "html" in response.headers.get("Content-Type", "html"):
            text = KIWIX_FOOTER.sub("", sources.html_to_text(response.text)).strip()
            if text:
                return text, response.url
    return None, None


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
        text = sources.pdf_text(page.content)
    else:
        text = sources.html_to_text(page.text)
    return {"text": text, "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "link": closest["url"]}


def month_name(date):
    try:
        return datetime.strptime(date[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        return date


def compare_with_before(target):
    """Compare a page today with an older copy of it. `target` is a Wikipedia
    link (or article title) or any web link. Raises sources.SourceError if no
    older copy can be found. Returns a dict ready to be saved as a before_check."""
    target = target.strip()
    if not target:
        raise sources.SourceError("Paste a link first.")
    found = None
    if sources.is_wikipedia_link(target) or "://" not in target:
        lang, title = sources.parse_wikipedia_target(target)
        wiki = sources.Wikipedia(lang)
        rev = wiki.revision_at(title)
        title, url = rev["title"], wiki.article_url(rev["title"])
        today = wiki.text_of(rev["revid"])
        after_link = wiki.revision_url(rev["revid"])
        copy = kiwix_copy(lang)
        if copy:
            text, link = kiwix_article(copy, title)
            if text is None and title != sources.parse_wikipedia_target(target)[1]:
                text, link = kiwix_article(copy, sources.parse_wikipedia_target(target)[1])  # an older name
            if text:
                found = {"source": "kiwix", "text": text, "before_date": copy["date"], "before_link": link,
                         "download_link": copy["download"],
                         "source_name": f"Kiwix's offline {WIKIPEDIA_NAMES.get(lang, lang)} Wikipedia ({month_name(copy['date'])})"}
    else:
        url = target if "://" in target else "https://" + target
        page = sources.fetch_page(url)
        if page["status"] == "removed":
            raise sources.SourceError("That page doesn't exist any more (it answered 'not found'). "
                                      "Try its link on web.archive.org to see older copies.")
        title, today, after_link = url.split("://", 1)[1].rstrip("/"), page["text"], url
    if not found:
        old = archive_copy(url)
        if old:
            found = {"source": "archive", "text": old["text"], "before_date": old["date"],
                     "before_link": old["link"], "download_link": None,
                     "source_name": f"the Internet Archive's copy ({month_name(old['date'])})"}
    if not found:
        raise sources.SourceError("Tripwire couldn't find an older copy of that page, in Kiwix or the "
                                  "Internet Archive. You can keep a copy from today in My shelf.")

    result = changes.compare_long(found["text"], today)
    swaps = changes.swap_counts(result["replacements"])
    if not result["hunks"]:
        summary = "No differences in the words since then."
    else:
        summary = changes.describe(result)
    details = {"swaps": swaps[:30], "hunks": result["hunks"][:200], "hunk_count": len(result["hunks"]),
               "kinds": changes.kind_counts(result["hunks"]), "name_changes": result["name_changes"],
               "words_before": len(found["text"].split()), "words_after": len(today.split()),
               "after_link": after_link}
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


def checks(conn, user_id):
    return conn.execute("""SELECT id, url, title, source, source_name, before_date, category, summary, created_at
                           FROM before_checks WHERE user_id = ? ORDER BY id DESC""", (user_id,)).fetchall()


def own(conn, user_id, check_id):
    return conn.execute("SELECT * FROM before_checks WHERE id = ? AND user_id = ?", (check_id, user_id)).fetchone()

"""Fetch things from the internet: Wikipedia articles, ordinary web pages, and the
Internet Archive.

Everything that touches the network lives here, so the rest of Tripwire can be
tested without an internet connection.
"""

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlparse

import requests

# Wikipedia and the Internet Archive ask tools to identify themselves.
USER_AGENT = "Tripwire/0.1 (Edge City Buildathon; information preservation tool)"
TIMEOUT = 30


class SourceError(Exception):
    """Something went wrong fetching a source (offline, blocked, not found...)."""


def _get(url, **kwargs):
    kwargs.setdefault("timeout", TIMEOUT)
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    try:
        return requests.get(url, headers=headers, **kwargs)
    except requests.RequestException as exc:
        raise SourceError(f"Could not reach {urlparse(url).netloc}: {exc}") from exc


# ---------------------------------------------------------------------------
# Turning a web page (HTML) into plain readable text
# ---------------------------------------------------------------------------

class _TextExtractor(HTMLParser):
    # Tags whose contents are never readable text.
    SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}
    # Wikipedia clutter: footnote markers like [1] and "edit" links.
    SKIP_CLASSES = {"reference", "mw-editsection", "noprint", "mw-jump-link"}
    # Tags that never have a closing tag, so they can't be "skipped into".
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                 "link", "meta", "source", "track", "wbr"}
    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr",
                  "br", "section", "article", "blockquote", "dd", "dt", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID_TAGS:
            if tag == "br":
                self.parts.append("\n")
            return
        classes = set((dict(attrs).get("class") or "").split())
        if self.skip_depth or tag in self.SKIP_TAGS or classes & self.SKIP_CLASSES:
            self.skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.VOID_TAGS:
            return
        if self.skip_depth:
            self.skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html or "")
    parser.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------

def parse_wikipedia_target(text):
    """Accept either an article title ("Adivasi") or a Wikipedia link
    ("https://gom.wikipedia.org/wiki/...") and return (language, title).
    A plain title is assumed to be on English Wikipedia."""
    text = text.strip()
    url = urlparse(text if "://" in text else "https://" + text)
    match = re.fullmatch(r"([a-z\-]+)(?:\.m)?\.wikipedia\.org", url.netloc)
    if match and url.path.startswith("/wiki/"):
        return match.group(1), unquote(url.path[len("/wiki/"):]).replace("_", " ")
    if "://" in text:
        raise SourceError("That link doesn't look like a Wikipedia article.")
    return "en", text


def is_wikipedia_link(text):
    return bool(re.search(r"(^|//)[a-z\-]+(\.m)?\.wikipedia\.org/wiki/", text.strip()))


class Wikipedia:
    def __init__(self, lang="en"):
        self.lang = lang
        self.base = f"https://{lang}.wikipedia.org"

    def _api(self, **params):
        params.update(format="json", formatversion=2)
        response = _get(self.base + "/w/api.php", params=params)
        if response.status_code != 200:
            raise SourceError(f"Wikipedia answered with error {response.status_code}")
        data = response.json()
        if "error" in data:
            raise SourceError(f"Wikipedia: {data['error'].get('info', 'unknown error')}")
        return data

    def article_url(self, title):
        return f"{self.base}/wiki/{quote(title.replace(' ', '_'))}"

    def revision_url(self, revid):
        """A permanent link to one exact version of an article."""
        return f"{self.base}/w/index.php?oldid={revid}"

    def diff_url(self, old_revid, new_revid):
        """Wikipedia's own side-by-side comparison of two versions."""
        return f"{self.base}/w/index.php?diff={new_revid}&oldid={old_revid}"

    def revision_at(self, title, days_ago=0):
        """The version of an article as it was `days_ago` days ago (0 = now).
        Returns {"title", "revid", "timestamp"}; title is the official spelling
        after following redirects."""
        params = dict(action="query", prop="revisions", titles=title, redirects=1,
                      rvprop="ids|timestamp", rvlimit=1, rvdir="older")
        if days_ago:
            moment = datetime.now(timezone.utc) - timedelta(days=days_ago)
            params["rvstart"] = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        page = self._api(**params)["query"]["pages"][0]
        if page.get("missing"):
            raise SourceError(f"There's no article called “{title}” on {self.lang}.wikipedia.org")
        if not page.get("revisions"):
            # The article didn't exist that long ago: start from its first version.
            params.pop("rvstart")
            params["rvdir"] = "newer"
            page = self._api(**params)["query"]["pages"][0]
        rev = page["revisions"][0]
        return {"title": page["title"], "revid": rev["revid"], "timestamp": rev["timestamp"]}

    def revisions_since(self, title, revid):
        """Every version saved after `revid`, oldest first, with who made it and
        their edit summary."""
        data = self._api(action="query", prop="revisions", titles=title, redirects=1,
                         rvprop="ids|timestamp|user|comment", rvlimit=50,
                         rvdir="newer", rvstartid=revid)
        page = data["query"]["pages"][0]
        if page.get("missing"):
            return None  # the article was deleted
        return [r for r in page.get("revisions", []) if r["revid"] != revid]

    def text_of(self, revid):
        """The readable text of one exact version of an article."""
        data = self._api(action="parse", oldid=revid, prop="text", disablelimitreport=1)
        return html_to_text(data["parse"]["text"])


# ---------------------------------------------------------------------------
# Ordinary web pages
# ---------------------------------------------------------------------------

def fetch_page(url):
    """Download a web page. Returns {"status": "ok", "text": ...} or
    {"status": "removed", "code": 404} when the page is gone."""
    response = _get(url)
    if response.status_code in (404, 410):
        return {"status": "removed", "code": response.status_code}
    if response.status_code >= 400:
        raise SourceError(f"{urlparse(url).netloc} answered with error {response.status_code}")
    return {"status": "ok", "text": html_to_text(response.text)}


# ---------------------------------------------------------------------------
# Internet Archive (Wayback Machine)
# ---------------------------------------------------------------------------

def archive_save(url):
    """Ask the Internet Archive to save a copy of `url` right now.
    Returns the link to the saved copy. This can take up to a minute."""
    response = _get("https://web.archive.org/save/" + url, timeout=120)
    if response.status_code >= 400:
        raise SourceError(f"The Internet Archive couldn't save the page (error {response.status_code})")
    location = response.headers.get("Content-Location")
    if location:
        return "https://web.archive.org" + location
    if "/web/" in response.url:
        return response.url
    return archive_latest(url)


def archive_latest(url):
    """Link to the most recent Internet Archive copy of `url`, or None."""
    response = _get("https://archive.org/wayback/available", params={"url": url})
    if response.status_code != 200:
        return None
    closest = response.json().get("archived_snapshots", {}).get("closest")
    return closest["url"] if closest and closest.get("available") else None

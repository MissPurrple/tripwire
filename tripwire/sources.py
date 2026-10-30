"""Fetch things from the internet: Wikipedia articles, ordinary web pages, and the
Internet Archive.

Everything that touches the network lives here, so the rest of Tripwire can be
tested without an internet connection.
"""

import ipaddress
import json
import re
import socket
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

# Wikipedia and the Internet Archive ask tools to identify themselves.
USER_AGENT = "Tripwire/0.1 (Edge City Buildathon; information preservation tool)"
TIMEOUT = 30


class SourceError(Exception):
    """Something went wrong fetching a source (offline, blocked, not found...)."""


MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # one download; anything bigger is refused, not read
MAX_REDIRECTS = 5


def _check_public(url):
    """Only ordinary web links to the public internet. Links that point inside a private
    network (the server itself, Vercel's machinery, a home router) are refused, so nobody
    can use Tripwire to reach places they couldn't reach themselves."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceError("Tripwire only opens ordinary web links (starting with http:// or https://).")
    try:
        found = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        if requests.utils.get_environ_proxies(url) and not _looks_like_address(parts.hostname):
            return  # behind a proxy (some offices, some laptops): the proxy looks the name up, not us
        raise SourceError(f"Couldn't find {parts.hostname}. Check the link, or try again later.") from exc
    for *_, address in found:
        ip = ipaddress.ip_address(address[0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise SourceError("That link points inside a private network, so Tripwire won't open it.")


def _looks_like_address(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _get(url, **kwargs):
    """Fetch a link. Every hop of a redirect is checked with _check_public, and a download
    stops at MAX_DOWNLOAD_BYTES. With stream=True the caller reads (and limits) it itself."""
    kwargs.setdefault("timeout", TIMEOUT)
    stream = kwargs.pop("stream", False)
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _check_public(url)
            response = requests.get(url, headers=headers, allow_redirects=False, stream=True, **kwargs)
            location = response.headers.get("Location")
            if not (response.is_redirect and location):
                break
            response.close()
            following = urljoin(response.url, location)
            if urlparse(following).hostname != urlparse(url).hostname:
                headers.pop("Authorization", None)  # a password for one site is never sent to another
            url = following
            kwargs.pop("params", None)  # they belonged to the first address
        else:
            raise SourceError("That link sends Tripwire round in circles (too many redirects).")
        if not stream:
            _read_limited(response)
        return response
    except requests.RequestException as exc:
        raise SourceError(f"Couldn't reach {urlparse(url).netloc}. Check the link, or try again later.") from exc


def _read_limited(response):
    too_big = SourceError(f"That's too big for Tripwire to fetch (over {MAX_DOWNLOAD_BYTES // 1048576} MB).")
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
        response.close()
        raise too_big
    chunks, total = [], 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            response.close()
            raise too_big
        chunks.append(chunk)
    response._content = b"".join(chunks)  # from here on it behaves like an ordinary, fully read response
    response._content_consumed = True


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

def fetch_page(url, browser=None):
    """Download a web page. Returns {"status": "ok", "text": ..., "browser": bool} or
    {"status": "removed", "code": 404} when the page is gone.

    browser: True = read it the way a browser does (this page fills in its words after it
    opens; see render.py), False = never, None = decide now: only if the plain download
    looks unfinished and a browser finds clearly more. "browser" in the result says which was used."""
    response = _get(url)
    if response.status_code in (404, 410):
        return {"status": "removed", "code": response.status_code}
    if response.status_code >= 400:
        raise SourceError(f"{urlparse(url).netloc} answered with error {response.status_code}")
    kind = response.headers.get("Content-Type", "").lower()
    if "pdf" in kind or response.content[:5] == b"%PDF-":
        return {"status": "ok", "text": pdf_text(response.content)}
    if "epub" in kind or (response.content[:2] == b"PK" and urlparse(url).path.lower().endswith(".epub")):
        from . import books  # an e-book at a link (watched from Books & videos)
        try:
            return {"status": "ok", "text": books.read_book("book.epub", response.content)[0]}
        except books.BookError as exc:
            raise SourceError(str(exc)) from exc
    if kind.startswith("text/plain"):
        return {"status": "ok", "text": response.text.strip(), "browser": False}
    html = response.text
    text = html_to_text(html)
    from . import render
    if browser or (browser is None and render.looks_unfinished(html, text) and render.available()):
        try:
            seen = render.render(url)
        except render.RenderError:
            if browser:
                raise  # this page needs a browser: better to skip today's check than record an empty page
        else:
            if browser or render.better(text, seen["text"]):
                return {"status": "ok", "text": seen["text"].strip(), "browser": True}
    return {"status": "ok", "text": text, "browser": False}


def pdf_text(data):
    """The text of a PDF found on the web (a gazette, a memo). A scanned PDF has
    no text to compare, so it's described by its pages and a fingerprint of the
    file instead: Tripwire still notices if it is replaced or removed."""
    import hashlib
    import io
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        pages = len(reader.pages)
    except Exception:  # a damaged PDF: fall back to the fingerprint
        text, pages = "", None
    if len(text.split()) >= 20:
        return text
    fingerprint = hashlib.sha256(data).hexdigest()[:16]
    return (f"Scanned PDF ({pages} pages). Its words can't be read here, so Tripwire compares its fingerprint: "
            f"{fingerprint}. To compare the words, download it and use Books.")


# ---------------------------------------------------------------------------
# Internet Archive (Wayback Machine)
# ---------------------------------------------------------------------------

def archive_save(url, timeout=120):
    """Ask the Internet Archive to save a copy of `url` right now.
    Returns the link to the saved copy. This can take up to a minute."""
    response = _get("https://web.archive.org/save/" + url, timeout=timeout)
    if response.status_code >= 400:
        raise SourceError(f"The Internet Archive couldn't save the page (error {response.status_code})")
    location = response.headers.get("Content-Location")
    if location:
        return "https://web.archive.org" + location
    if "/web/" in response.url:
        return response.url
    return None


def archive_latest(url):
    """Link to the most recent Internet Archive copy of `url`, or None."""
    response = _get("https://archive.org/wayback/available", params={"url": url})
    if response.status_code != 200:
        return None
    closest = response.json().get("archived_snapshots", {}).get("closest")
    return closest["url"] if closest and closest.get("available") else None


# ---------------------------------------------------------------------------
# Videos: YouTube and TikTok
# ---------------------------------------------------------------------------

YOUTUBE_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{11})")


def video_platform(url):
    host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    if YOUTUBE_ID_RE.search(url) and ("youtube.com" in host or "youtu.be" in host):
        return "YouTube"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "TikTok"
    return None


def video_status(url):
    """What a video looks like publicly right now. Returns a dict:
      platform, url, title, author,
      available   True / False
      reason      why it isn't available, in the platform's words if given
      restricted  True if age-restricted or marked sensitive, False if not,
                  None if the platform doesn't say
      unlisted    True / False / None (YouTube only)
    """
    url = url.strip()
    platform = video_platform(url)
    if platform == "YouTube":
        return _youtube_status(YOUTUBE_ID_RE.search(url).group(1))
    if platform == "TikTok":
        return _tiktok_status(url if "://" in url else "https://" + url)
    raise SourceError("That isn't a YouTube or TikTok video link.")


def _video_state(platform, url):
    return {"platform": platform, "url": url, "title": None, "author": None,
            "available": True, "reason": "", "restricted": None, "unlisted": None}


def _youtube_status(video_id):
    state = _video_state("YouTube", f"https://www.youtube.com/watch?v={video_id}")
    response = _get("https://www.youtube.com/oembed", params={"url": state["url"], "format": "json"})
    if response.status_code == 429 or response.status_code >= 500:
        raise SourceError("YouTube is busy right now. Tripwire will try again on the next check.")
    blocked = False
    if response.status_code == 200:
        data = response.json()
        state["title"], state["author"] = data.get("title"), data.get("author_name")
    elif response.status_code in (400, 404):
        state["available"] = False
        state["reason"] = "YouTube says this video doesn't exist any more."
    else:
        blocked = True  # private, or the owner turned off sharing; the page below can tell which

    # The public video page says more: age restriction, private, unlisted, and why it was removed.
    player = None
    try:
        page = _get(state["url"], headers={"Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
        player = _embedded_json(page.text, "ytInitialPlayerResponse")
    except SourceError:
        pass
    if player:
        details = player.get("videoDetails") or {}
        state["title"] = details.get("title") or state["title"]
        state["author"] = details.get("author") or state["author"]
        micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
        if "isFamilySafe" in micro:
            state["restricted"] = not micro["isFamilySafe"]
        if "isUnlisted" in micro:
            state["unlisted"] = bool(micro["isUnlisted"])
        playability = player.get("playabilityStatus") or {}
        status, reason = playability.get("status"), _youtube_reason(playability)
        if status == "OK":
            state["available"], state["reason"] = True, ""
        elif status == "CONTENT_CHECK_REQUIRED" or (status == "LOGIN_REQUIRED" and "age" in reason.lower()):
            state["available"], state["restricted"] = True, True
        elif status == "LOGIN_REQUIRED" and "bot" in reason.lower():
            pass  # YouTube asked Tripwire to prove it's human; keep what oEmbed said
        elif status:
            state["available"], state["reason"] = False, reason or "YouTube says this video is unavailable."
    elif blocked:
        state["available"] = False
        state["reason"] = "YouTube won't show this video publicly (it may have been made private)."
    return state


def _youtube_reason(playability):
    if isinstance(playability.get("reason"), str):
        return playability["reason"]
    renderer = ((playability.get("errorScreen") or {}).get("playerErrorMessageRenderer") or {})
    reason = renderer.get("reason") or {}
    if "simpleText" in reason:
        return reason["simpleText"]
    return "".join(run.get("text", "") for run in reason.get("runs", []))


def _embedded_json(html, name):
    """Pull a JSON object like `var ytInitialPlayerResponse = {...};` out of a page."""
    start = html.find(name + " = {")
    if start == -1:
        start = html.find(name + "={")
    if start == -1:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[html.index("{", start):])[0]
    except ValueError:
        return None


def _tiktok_status(url):
    response = _get("https://www.tiktok.com/oembed", params={"url": url})
    if response.status_code == 429 or response.status_code >= 500:
        raise SourceError("TikTok is busy right now. Tripwire will try again on the next check.")
    state = _video_state("TikTok", url)
    if response.status_code == 200:
        data = response.json()
        state["title"], state["author"] = data.get("title"), data.get("author_name")
    else:
        state["available"] = False
        state["reason"] = "TikTok says this video isn't available (it may have been removed or made private)."
    return state


def video_text(state):
    """A plain-words description of a video's state, kept as the 'before' and
    'after' text of a change."""
    def yes_no(value):
        return "unknown" if value is None else ("yes" if value else "no")
    lines = [f"Title: {state['title'] or '(unknown)'}",
             f"{'Channel' if state['platform'] == 'YouTube' else 'Account'}: {state['author'] or '(unknown)'}",
             f"Available: {'yes' if state['available'] else 'no'}" + (f" ({state['reason']})" if state["reason"] else ""),
             f"Age-restricted or marked sensitive: {yes_no(state['restricted'])}"]
    if state["platform"] == "YouTube":
        lines.append(f"Unlisted: {yes_no(state['unlisted'])}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Open Library: free facts about books (the Internet Archive's book catalogue)
# ---------------------------------------------------------------------------

OPEN_LIBRARY = "https://openlibrary.org"


def open_library_facts(title=None, authors=None, isbn=None):
    """Quick facts about a book from Open Library, or None if it can't be found.
    Never raises: facts are a bonus, not something a comparison depends on."""
    try:
        facts = {}
        if isbn:
            response = _get(f"{OPEN_LIBRARY}/isbn/{isbn}.json", timeout=15)
            if response.status_code == 200:
                edition = response.json()
                facts.update(edition_publisher=", ".join(edition.get("publishers", [])[:2]) or None,
                              edition_date=edition.get("publish_date"),
                              edition_pages=edition.get("number_of_pages"))
        if title:
            params = {"title": title, "limit": 1,
                      "fields": "key,title,author_name,first_publish_year,edition_count,publisher,"
                                "subject,number_of_pages_median"}
            if authors:
                params["author"] = authors.split(",")[0].split(";")[0].strip()
            response = _get(f"{OPEN_LIBRARY}/search.json", params=params, timeout=15)
            docs = response.json().get("docs", []) if response.status_code == 200 else []
            if docs:
                doc = docs[0]
                facts.update(url=OPEN_LIBRARY + doc["key"], title=doc.get("title"),
                              authors=", ".join(doc.get("author_name", [])[:3]) or None,
                              first_published=doc.get("first_publish_year"),
                              editions=doc.get("edition_count"),
                              typical_pages=doc.get("number_of_pages_median"),
                              publishers=", ".join(doc.get("publisher", [])[:3]) or None,
                              subjects=doc.get("subject", [])[:5])
        return {k: v for k, v in facts.items() if v} or None
    except (SourceError, ValueError, KeyError, TypeError):
        return None

"""Books: read two editions of a book and compare them word by word.

Only ever run when a person asks. Tripwire keeps the comparison (the changes,
with a few words of context each) but never stores the books themselves.
"""

import io
import posixpath
import re
import zipfile
from urllib.parse import urlparse
from xml.etree import ElementTree

from . import sources

MAX_BOOK_BYTES = 40 * 1024 * 1024  # a book bigger than this is almost certainly not text


class BookError(ValueError):
    """The book couldn't be read. The message says why, in plain words."""


def read_book(filename, data):
    """Turn a book file into plain text. Returns (text, info) where info says
    the format and, for PDFs, the number of pages."""
    name = (filename or "").lower()
    if not data:
        raise BookError(f"“{filename}” is empty.")
    if name.endswith(".epub") or data[:2] == b"PK":
        text, info = _read_epub(data)
    elif name.endswith(".pdf") or data[:5] == b"%PDF-":
        text, info = _read_pdf(data)
    elif name.endswith((".html", ".htm")) or data.lstrip()[:1] == b"<":
        text, info = sources.html_to_text(_decode(data)), {"format": "web page"}
    else:
        text, info = _decode(data), {"format": "plain text"}
    text, gutenberg = strip_gutenberg(text)
    if gutenberg:
        info.update({k: v for k, v in gutenberg.items() if v and not info.get(k)})
        info["source"] = "Project Gutenberg"
    text = text.strip()
    if len(text.split()) < 20:
        raise BookError(f"Couldn't find readable text in “{filename}”. If it's a scanned PDF (pictures of "
                        "pages), Tripwire can't read it yet. A text, EPUB or text-based PDF works best.")
    info["words"] = len(text.split())
    if not info.get("pages"):
        info["pages_estimate"] = max(1, round(info["words"] / WORDS_PER_PAGE))
    return text, info


WORDS_PER_PAGE = 300  # a typical printed page, used only to estimate when the file doesn't say

GUTENBERG_START = re.compile(r"^\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)
GUTENBERG_END = re.compile(r"^\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*$", re.I | re.M)


def strip_gutenberg(text):
    """Project Gutenberg files wrap the book in a licence header and footer whose
    dates and wording differ between files. Keep just the book, plus the
    details the header gives. Returns (text, details or None)."""
    start, end = GUTENBERG_START.search(text), GUTENBERG_END.search(text)
    if not start:
        return text, None
    header = text[:start.start()]

    def field(*names):
        for name in names:
            m = re.search(rf"^{name}:\s*(.+(?:\n[ \t]+.+)*)", header, re.I | re.M)
            if m:
                return " ".join(m.group(1).split())
        return None

    original = field("Original publication") or ""
    year = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", original)
    details = {"title": field("Title"), "authors": field("Author", "Authors"), "language": field("Language"),
               "released": field("Release date", "Release Date", "Posting Date"),
               "year": year.group(1) if year else None,
               "publisher": re.sub(r",?\s*\b(1[5-9]\d\d|20\d\d)\b.*$", "", original).strip(" ,.") or None}
    body = text[start.end():end.start() if end else len(text)]
    return body, details


def fetch_book(url):
    """Download a book from a link. Returns (filename, data)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise BookError("Links need to start with http:// or https://")
    response = sources._get(url, timeout=60, stream=True)
    if response.status_code >= 400:
        raise BookError(f"That link answered with error {response.status_code}.")
    data = b""
    try:
        for chunk in response.iter_content(1024 * 1024):
            data += chunk
            if len(data) > MAX_BOOK_BYTES:
                raise BookError("That file is too big to be a book (over 40 MB).")
    except sources.requests.RequestException as exc:
        raise BookError("The download was interrupted. Try again.") from exc
    filename = posixpath.basename(urlparse(response.url).path) or "book"
    content_type = response.headers.get("Content-Type", "")
    if "epub" in content_type and not filename.endswith(".epub"):
        filename += ".epub"
    elif "pdf" in content_type and not filename.endswith(".pdf"):
        filename += ".pdf"
    elif "html" in content_type and not filename.endswith((".html", ".htm")):
        filename += ".html"
    return filename, data


def _decode(data):
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue


def _read_pdf(data):
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        meta = reader.metadata or {}
    except Exception as exc:  # pypdf raises many kinds of errors for broken files
        raise BookError(f"Couldn't open that PDF ({exc.__class__.__name__}). It may be damaged or locked.") from exc
    info = {"format": "PDF", "pages": len(pages)}
    if meta.get("/Title"):
        info["title"] = str(meta["/Title"]).strip() or None
    if meta.get("/Author"):
        info["authors"] = str(meta["/Author"]).strip() or None
    found = re.search(r"(\d{4})", str(meta.get("/CreationDate") or ""))
    if found:
        info["file_year"] = found.group(1)
    return "\n\n".join(pages), info


def _read_epub(data):
    """An EPUB is a zip of web pages. Read them in the book's own reading order."""
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
        container = ElementTree.fromstring(book.read("META-INF/container.xml"))
        opf_path = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
        opf = ElementTree.fromstring(book.read(opf_path))
    except (zipfile.BadZipFile, KeyError, StopIteration, ElementTree.ParseError) as exc:
        raise BookError("Couldn't open that EPUB. It may be damaged or protected (DRM).") from exc
    info = {"format": "EPUB"}
    for key, tag in (("title", "title"), ("authors", "creator"), ("publisher", "publisher"),
                     ("year", "date"), ("language", "language")):
        values = [el.text.strip() for el in opf.iter() if el.tag.endswith("}" + tag) and el.text and el.text.strip()]
        if values:
            info[key] = ", ".join(dict.fromkeys(values)) if key == "authors" else values[0]
    if info.get("year"):
        found = re.search(r"\d{4}", info["year"])
        info["year"] = found.group(0) if found else None
    isbn = next((re.sub(r"[^\dX]", "", el.text) for el in opf.iter()
                 if el.tag.endswith("}identifier") and el.text and re.search(r"97[89][\d-]{10,}|\b\d{9}[\dX]\b", el.text)), None)
    if isbn:
        info["isbn"] = isbn
    base = posixpath.dirname(opf_path)
    manifest = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("item")}
    order = [el.get("idref") for el in opf.iter() if el.tag.endswith("itemref")]
    parts = []
    for item_id in order:
        href = manifest.get(item_id)
        if not href:
            continue
        try:
            html = book.read(posixpath.normpath(posixpath.join(base, href)))
        except KeyError:
            continue
        parts.append(sources.html_to_text(_decode(html)))
    return "\n\n".join(parts), info


def edition_label(filename, info):
    detail = info["format"]
    if info.get("pages"):
        detail += f", {info['pages']} pages"
    return f"{filename} ({detail}, {info['words']:,} words)"


def clean_name(filename):
    return re.sub(r"[^\w .,()\-']", "", posixpath.basename(filename or "book"))[:120] or "book"

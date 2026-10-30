"""My shelf: a person's own copies of the documents and pages they care about
(a gazette, a memo, a Wikipedia article), and "Take my shelf with me": all of it
as one file they own, so nothing depends on Tripwire, or the original website,
still being there.

The download comes two ways, with the same pages inside:
  - a ZIP: open index.html in any web browser; the original files and plain
    text are in folders next to it.
  - a ZIM: the file format of Kiwix (kiwix.org), the free app that reads whole
    libraries with no internet, on phones, laptops, or a Raspberry Pi serving a
    school or village over Wi-Fi. Searchable inside Kiwix.
"""

import base64
import json
import mimetypes
import os
import re
import tempfile
import zipfile

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import books, db, sources, stamp as stamps

MAX_FILE_BYTES = 15 * 1024 * 1024    # one original file kept from a link
MAX_SHELF_BYTES = 60 * 1024 * 1024   # everything on one person's shelf
GUEST_SHELF_BYTES = 10 * 1024 * 1024  # the same, for a visitor without an account (so bots can't fill the database)
MAX_TEXT_CHARS = 2_000_000           # the words of one item (a long book is about 1 million)
LIST_COLUMNS = ["id", "user_id", "title", "note", "source_url", "file_name", "file_size", "text_from",
                "watch_id", "before_check_id", "created_at", "locked", "sealed_title", "stamp"]
# Locked items arrive already locked (see tripwire/export_files/vault-core.js): text that looks like
# this, and nothing else. Tripwire can't read them; it only checks they're the right shape and size.
SEALED = re.compile(r"[A-Za-z0-9+/]+={0,2}")
MAX_SEALED_TITLE = 4000
MAX_SEALED_CHARS = MAX_TEXT_CHARS * 2 + 100_000


def _columns(prefix=""):
    """The columns to read, without the file itself (it can be big)."""
    return ", ".join(prefix + c for c in LIST_COLUMNS) + f", LENGTH({prefix}text) AS text_length"

HERE = os.path.dirname(__file__)
EXPORT_TEMPLATES = os.path.join(HERE, "..", "templates", "export")
# Inside the tripwire folder (not public/static) so Vercel includes them with the app itself.
STATIC = os.path.join(HERE, "export_files")
# The Kiwix language codes (3 letters) for the site's languages.
ZIM_LANGUAGES = {"en": "eng", "hi": "hin", "st": "sot"}


class ShelfError(ValueError):
    """Something couldn't be added. The message says why, in plain words."""


def file_type(filename):
    return mimetypes.guess_type(filename or "")[0] or "application/octet-stream"


def safe_name(text, fallback="item"):
    """Letters, numbers and dashes only: safe as a file name anywhere."""
    name = re.sub(r"[^\w.-]+", "-", text or "", flags=re.ASCII).strip("-.")[:60]
    return name or fallback


class Shelf:
    def __init__(self, conn):
        self.conn = conn

    # -- reading ------------------------------------------------------------

    def items(self, user_id, search=""):
        """The person's shelf, newest first, without the files themselves.
        `search` looks in titles, notes and the words of each item."""
        query = f"""SELECT {_columns("s.")}, w.last_status AS watch_status
                    FROM shelf_items s LEFT JOIN watches w ON w.id = s.watch_id WHERE s.user_id = ?"""
        params = [user_id]
        search = search.strip()
        if search:  # locked items can't be searched here: Tripwire can't read them (the browser can)
            query += " AND s.locked = 0 AND (LOWER(s.title) LIKE ? OR LOWER(s.note) LIKE ? OR LOWER(s.text) LIKE ?)"
            params += [f"%{search.lower()}%"] * 3
        return self.conn.execute(query + " ORDER BY s.id DESC", params).fetchall()

    def item(self, user_id, item_id):
        """One item, with its words (but not its file)."""
        return self.conn.execute(
            f"SELECT {_columns()}, text FROM shelf_items WHERE id = ? AND user_id = ?",
            (item_id, user_id)).fetchone()

    def file(self, user_id, item_id):
        row = self.conn.execute("SELECT file_name, file_data FROM shelf_items WHERE id = ? AND user_id = ?",
                                (item_id, user_id)).fetchone()
        return (row["file_name"], bytes(row["file_data"])) if row and row["file_data"] is not None else None

    def used_bytes(self, user_id):
        row = self.conn.execute("SELECT SUM(file_size + LENGTH(text)) AS n FROM shelf_items WHERE user_id = ?",
                                (user_id,)).fetchone()
        return int(row["n"] or 0)

    # -- adding ---------------------------------------------------------------

    def _check_room(self, user_id, size):
        guest = self.conn.execute("SELECT guest FROM users WHERE id = ?", (user_id,)).fetchone()
        if guest and guest["guest"]:
            if self.used_bytes(user_id) + size > GUEST_SHELF_BYTES:
                raise ShelfError(f"Without an account, a shelf holds up to {GUEST_SHELF_BYTES // 1048576} MB. "
                                 f"Make an account (it's free) for {MAX_SHELF_BYTES // 1048576} MB.")
        if self.used_bytes(user_id) + size > MAX_SHELF_BYTES:
            raise ShelfError(f"Your shelf is full (it holds up to {MAX_SHELF_BYTES // 1048576} MB). "
                             "Download it to keep everything, then remove a few big items to make room.")

    def _insert(self, user_id, title, note, text, text_from, source_url=None, file_name=None, data=None,
                watch_id=None, before_check_id=None, how="uploaded", wikipedia_version=None):
        """Keep a copy, with its signed stamp of originality. how: "fetched" (Tripwire fetched it
        itself from source_url) or "uploaded" (its owner gave it to Tripwire)."""
        text = (text or "")[:MAX_TEXT_CHARS]
        title = title.strip()[:200] or "Untitled"
        self._check_room(user_id, len(data or b"") + len(text))
        taken_at = db.now()
        stamp = stamps.Stamper(self.conn).for_copy(
            title=title, how=how, text=text, data=data, file_name=file_name, source_url=source_url,
            text_from=text_from, wikipedia_version=wikipedia_version, taken_at=taken_at)
        return self.conn.insert(
            """INSERT INTO shelf_items (user_id, title, note, source_url, file_name, file_size, file_data,
                   text, text_from, watch_id, before_check_id, created_at, stamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, title, note.strip()[:2000], source_url, file_name,
             len(data or b""), data, text, text_from, watch_id, before_check_id, taken_at, json.dumps(stamp)))

    def add_file(self, user_id, filename, data, title="", note="", read_text=None, pages=None):
        """Keep an uploaded file. `read_text` is the text of a scanned PDF or a photo,
        already read on the person's own device (see scan-reader.js)."""
        filename = books.clean_name(filename or "file")
        if not data and not read_text:
            raise ShelfError("Choose a file first.")
        if read_text:
            text = read_text
            text_from = f"Read on your device (scanned, {pages} page{'s' if int(pages) != 1 else ''})" if pages else "Read on your device"
        else:
            try:
                text, info = books.read_book(filename, data)
                text_from = f"Read from the file ({info['format']})"
            except books.BookError:
                text, text_from = "", None  # a scan or photo with no words yet: it can be read later
        return self._insert(user_id, title or os.path.splitext(filename)[0], note, text, text_from,
                            file_name=filename if data else None, data=data or None)

    def add_link(self, user_id, url, title="", note="", watch_id=None, before_check_id=None):
        """Keep a copy of what a link says today. A PDF at the link is kept whole."""
        url = url.strip()
        if sources.is_wikipedia_link(url):
            lang, article = sources.parse_wikipedia_target(url)
            wiki = sources.Wikipedia(lang)
            rev = wiki.revision_at(article)
            text = wiki.text_of(rev["revid"])
            return self._insert(user_id, title or rev["title"], note, text,
                                f"Wikipedia, version of {rev['timestamp'][:10]}",
                                source_url=wiki.article_url(rev["title"]), watch_id=watch_id,
                                before_check_id=before_check_id, how="fetched",
                                wikipedia_version=wiki.revision_url(rev["revid"]))
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            filename, data = books.fetch_book(url)
        except books.BookError as exc:
            raise ShelfError(str(exc).replace("to be a book (over 40 MB)", "to keep (over 40 MB)")) from exc
        if filename.lower().endswith(".pdf") or data[:5] == b"%PDF-":
            if len(data) > MAX_FILE_BYTES:
                raise ShelfError(f"That PDF is over {MAX_FILE_BYTES // 1048576} MB, too big to keep here. "
                                 "Download it, and keep it on your own device.")
            try:
                text, info = books.read_book(filename, data)
                text_from = "Read from the PDF"
            except books.BookError:
                text, text_from = "", None  # scanned: the person can read it on their device
            return self._insert(user_id, title or os.path.splitext(filename)[0], note, text, text_from,
                                source_url=url, file_name=filename, data=data, watch_id=watch_id,
                                before_check_id=before_check_id, how="fetched")
        page = books._decode(data)
        found = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
        text = sources.html_to_text(page) if page.lstrip()[:1] == "<" else page
        page_title = sources.html_to_text(found.group(1)) if found else url.split("://", 1)[1]
        return self._insert(user_id, title or page_title, note, text, f"The web page, as it was on {db.now()[:10]}",
                            source_url=url, watch_id=watch_id, before_check_id=before_check_id, how="fetched")

    def add_locked(self, user_id, sealed_title, sealed, data=None):
        """Keep an item that was locked in the person's browser. Tripwire stores it as it
        arrives and can't open it: no title, no words, no file name, only the locked parts."""
        if not self.conn.execute("SELECT vault FROM users WHERE id = ? AND vault IS NOT NULL", (user_id,)).fetchone():
            raise ShelfError("Set up your vault first.")
        for value, limit in ((sealed_title, MAX_SEALED_TITLE), (sealed, MAX_SEALED_CHARS)):
            if not value or len(value) > limit or not SEALED.fullmatch(value):
                raise ShelfError("That locked item didn't arrive whole. Please try again.")
        data = data or None
        if data and len(data) > MAX_FILE_BYTES:
            raise ShelfError(f"Files can be up to {MAX_FILE_BYTES // 1048576} MB.")
        size = len(data or b"") + len(sealed) + len(sealed_title)
        self._check_room(user_id, size)
        return self.conn.insert(
            """INSERT INTO shelf_items (user_id, title, note, file_name, file_size, file_data, text,
                   locked, sealed_title, sealed, created_at)
               VALUES (?, 'Locked item', '', ?, ?, ?, '', 1, ?, ?, ?)""",
            (user_id, "locked.bin" if data else None, size, data, sealed_title, sealed, db.now()))

    def sealed(self, user_id, item_id):
        return self.conn.execute("""SELECT id, sealed_title, sealed, file_data IS NOT NULL AS has_file, created_at
                                    FROM shelf_items WHERE id = ? AND user_id = ? AND locked = 1""",
                                 (item_id, user_id)).fetchone()

    def locked_items(self, user_id):
        """Everything locked, for taking with you (still locked)."""
        return self.conn.execute("""SELECT sealed_title, sealed, file_data, created_at FROM shelf_items
                                    WHERE user_id = ? AND locked = 1 ORDER BY id""", (user_id,)).fetchall()

    def set_text(self, user_id, item_id, text, pages=None):
        """Words read later on the person's device, for a scan that had none."""
        text = (text or "")[:MAX_TEXT_CHARS]
        self._check_room(user_id, len(text))
        text_from = f"Read on your device (scanned, {pages} page{'s' if int(pages) != 1 else ''})" if pages else "Read on your device"
        row = self.conn.execute("SELECT stamp FROM shelf_items WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone()
        stamp = json.loads(row["stamp"]) if row and row["stamp"] else None
        if stamp:  # the stamp now covers the words too, and says they were read later, on the owner's device
            fields = {k: v for k, v in stamp.items() if k not in ("kind", "version", "public_key", "key_id", "signature")}
            fields.update(text_sha256=stamps.fingerprint(text), text_from=text_from, text_added_at=db.now())
            stamp = stamps.Stamper(self.conn).make(**fields)
        self.conn.execute("UPDATE shelf_items SET text = ?, text_from = ?, stamp = ? WHERE id = ? AND user_id = ? AND locked = 0",
                          (text, text_from, json.dumps(stamp) if stamp else None, item_id, user_id))

    def update(self, user_id, item_id, title, note):
        self.conn.execute("UPDATE shelf_items SET title = ?, note = ? WHERE id = ? AND user_id = ? AND locked = 0",
                          (title.strip()[:200] or "Untitled", note.strip()[:2000], item_id, user_id))

    def delete(self, user_id, item_id):
        self.conn.execute("DELETE FROM shelf_items WHERE id = ? AND user_id = ?", (item_id, user_id))

    # -- taking it with you -------------------------------------------------

    def _export_pages(self, user_id, name, verify_url=None):
        """Every page and file of the download, as (path, title, mimetype, content, is_page)."""
        env = Environment(loader=FileSystemLoader(EXPORT_TEMPLATES), autoescape=select_autoescape())
        rows = self.conn.execute("SELECT * FROM shelf_items WHERE user_id = ? AND locked = 0 ORDER BY id",
                                 (user_id,)).fetchall()
        checks = {r["id"]: dict(r, details=db.entry_details(r)) for r in self.conn.execute(
            "SELECT * FROM before_checks WHERE user_id = ?", (user_id,)).fetchall()}
        watches = {r["id"]: r for r in self.conn.execute(
            "SELECT w.id, w.last_status, w.last_checked_at FROM watches w JOIN themes t ON t.id = w.theme_id "
            "WHERE t.user_id = ?", (user_id,)).fetchall()}
        made = db.now()[:10]
        items = []
        for n, row in enumerate(rows, start=1):
            stem = f"{n:04d}-{safe_name(row['title'])}"
            items.append({**{k: row[k] for k in ("title", "note", "source_url", "text", "text_from", "created_at",
                                                 "file_name", "file_size")},
                          "page": f"items/{stem}.html", "text_path": f"text/{stem}.txt",
                          "file_path": f"files/{n:04d}-{safe_name(row['file_name'])}" if row["file_name"] and row["file_data"] is not None else None,
                          "data": bytes(row["file_data"]) if row["file_data"] is not None else None,
                          "check": checks.get(row["before_check_id"]), "watch": watches.get(row["watch_id"]),
                          "stamp": json.loads(row["stamp"]) if row["stamp"] else None,
                          "stamp_path": f"stamps/{stem}.stamp.json" if row["stamp"] else None})
        files = [("index.html", name, "text/html",
                  env.get_template("index.html").render(name=name, items=items, made=made), True)]
        for item in items:
            files.append((item["page"], item["title"], "text/html",
                          env.get_template("item.html").render(name=name, item=item, made=made, verify_url=verify_url), True))
            if item["stamp"]:
                files.append((item["stamp_path"], f"Stamp: {item['title']}", "application/json",
                              json.dumps(item["stamp"], indent=2, ensure_ascii=False), False))
            if item["file_path"]:
                files.append((item["file_path"], item["file_name"], file_type(item["file_name"]), item["data"], False))
        for path in ("fonts/vt323-latin-400-normal.woff2", "fonts/LICENSE-VT323.txt", "rabbit.png"):
            with open(os.path.join(STATIC, path), "rb") as f:
                files.append((f"static/{path}", path, file_type(path) if not path.endswith(".woff2") else "font/woff2",
                              f.read(), False))
        return items, files

    def export_zip(self, user_id, name, verify_url=None):
        """Write the shelf as a ZIP file. Returns its path (delete it when done)."""
        items, files = self._export_pages(user_id, name, verify_url)
        handle, path = tempfile.mkstemp(suffix=".zip")
        os.close(handle)
        folder = safe_name(name, "my-shelf")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for file_path, _title, mime, content, _page in files:
                # Files that are already compressed (PDFs, photos, fonts) are stored as they are.
                packed = mime.startswith(("text/", "application/json"))
                z.writestr(f"{folder}/{file_path}", content, zipfile.ZIP_DEFLATED if packed else zipfile.ZIP_STORED)
            for item in items:
                z.writestr(f"{folder}/{item['text_path']}", item["text"] or "")
            listing = [{k: item[k] for k in ("title", "note", "source_url", "created_at", "text_from", "file_name",
                                             "page", "text_path", "file_path", "stamp_path")} for item in items]
            z.writestr(f"{folder}/shelf.json", json.dumps({"name": name, "made": db.now(), "items": listing},
                                                          indent=2, ensure_ascii=False))
            z.writestr(f"{folder}/README.txt", README.format(name=name, made=db.now()[:10], count=len(items)))
            locked = self.locked_items(user_id)
            if locked:  # still locked: only the person's passphrase or recovery code opens them
                vault = self.conn.execute("SELECT vault FROM users WHERE id = ?", (user_id,)).fetchone()["vault"]
                data = {"user": str(user_id), "vault": json.loads(vault),
                        "items": [{"sealed_title": r["sealed_title"], "sealed": r["sealed"], "created_at": r["created_at"],
                                   "file": base64.b64encode(bytes(r["file_data"])).decode() if r["file_data"] is not None else None}
                                  for r in locked]}
                as_json = json.dumps(data)
                z.writestr(f"{folder}/locked/locked.json", as_json)
                with open(os.path.join(STATIC, "vault-core.js"), encoding="utf-8") as f:
                    core = f.read()
                env = Environment(loader=FileSystemLoader(EXPORT_TEMPLATES), autoescape=select_autoescape())
                # "<" can't appear in the data inside the page, so nothing in it can end the script early.
                page = env.get_template("unlock.html").render(
                    name=name, made=db.now()[:10], count=len(locked), core=Markup(core.replace("</", "<\\/")),
                    data=Markup(as_json.replace("<", "\\u003c")))
                z.writestr(f"{folder}/locked/unlock.html", page)
        return path

    def export_zim(self, user_id, name, lang="en", verify_url=None):
        """Write the shelf as a ZIM file for Kiwix. Returns its path (delete it when done)."""
        from libzim.writer import Creator, Hint, Item, StringProvider

        class Page(Item):
            def __init__(self, path, title, mimetype, content, front):
                super().__init__()
                self.path, self.title, self.mimetype, self.content, self.front = path, title, mimetype, content, front

            def get_path(self):
                return self.path

            def get_title(self):
                return self.title

            def get_mimetype(self):
                return self.mimetype

            def get_contentprovider(self):
                return StringProvider(self.content)

            def get_hints(self):
                return {Hint.FRONT_ARTICLE: self.front}

        items, files = self._export_pages(user_id, name, verify_url)
        handle, path = tempfile.mkstemp(suffix=".zim")
        os.close(handle)
        os.remove(path)  # libzim makes the file itself
        with open(os.path.join(STATIC, "rabbit-48.png"), "rb") as f:
            icon = f.read()
        with Creator(path).config_indexing(True, ZIM_LANGUAGES.get(lang, "eng")) as creator:
            creator.set_mainpath("index.html")
            try:
                creator.add_illustration(icon, 48)   # libzim 4 and newer: the picture, then its size
            except TypeError:
                creator.add_illustration(48, icon)   # libzim 3: the size, then the picture
            for key, value in {"Name": f"tripwire_shelf_{safe_name(name, 'my-shelf').lower()}",
                               "Title": name[:30], "Language": ZIM_LANGUAGES.get(lang, "eng"),
                               "Description": f"{len(items)} documents and pages, kept with Tripwire"[:80],
                               "Creator": "Tripwire shelf", "Publisher": "Tripwire",
                               "Date": db.now()[:10], "Tags": "tripwire;archive;_pictures:yes"}.items():
                creator.add_metadata(key, value)
            for file_path, title, mime, content, page in files:
                creator.add_item(Page(file_path, title, mime, content, page))
        return path


README = """{name}
Made with Tripwire on {made}. {count} items.

To read it: open index.html in any web browser. It works with no internet.

  items/   one page per document or web page, with its words
  files/   the original files (PDFs, photos...), exactly as they were kept
  text/    the words of each item as plain text, to search or copy
  stamps/  each item's signed stamp of originality: where it came from, when, and a
           fingerprint of the copy. Check one on Tripwire's "Check a stamp" page.
  shelf.json   the list of items, for other programs
  locked/      items you locked in your vault, still locked: open locked/unlock.html
               in any browser and type your vault passphrase (works with no internet)

There is also a Kiwix version (.zim) of this shelf: download it from Tripwire's
My shelf page and open it with the free Kiwix app (kiwix.org), on a phone or a
computer, with no internet.
"""

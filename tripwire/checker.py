"""What Tripwire actually does: add things to watch, check them for changes,
save each change into the archive, and write the weekly summary."""

import json
import logging
import re
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from werkzeug.security import check_password_hash, generate_password_hash

from . import books, changes, db, live, sources

BOOK_FILE_DAYS = 7          # book files are deleted after this many days; results stay
BOOK_JOB_STUCK_MINUTES = 6  # a comparison "running" longer than this was interrupted: try again

MAX_THEMES = 3
MIN_PASSWORD = 8
SINGLE_LINKS = "Single links"
VIDEOS = "Videos"

# Changes big enough that Tripwire offers to keep a public Internet Archive copy.
# (Name spelling changes are only noted, never offered a copy.)
LONG_PAGE_WORDS = 10_000    # more words than this (before and after together): compared sentence by sentence
COPY_WORTHY = {"major", "removal", "page_removed"}


# Old-internet names, given at random: everyone is known by one of these, never by their email.
HANDLE_WORDS = (
    ["neon", "ghost", "cipher", "phreak", "glitch", "pixel", "null", "echo", "static", "velvet", "midnight", "binary",
     "silent", "lunar", "rogue", "chrome", "vapor", "dialup", "cosmic", "turbo", "hex", "quiet", "feral", "paper",
     "electric", "analog", "hidden", "wired", "solar", "acid"],
    ["rabbit", "moth", "fox", "wire", "byte", "raven", "signal", "comet", "modem", "ferret", "otter", "owl", "lynx",
     "node", "packet", "pilot", "spark", "tiger", "wolf", "drifter", "archivist", "scribe", "oracle", "lantern",
     "cassette", "floppy", "kernel", "daemon", "hare", "beacon"],
)


def new_handle():
    first, second = secrets.choice(HANDLE_WORDS[0]), secrets.choice(HANDLE_WORDS[1])
    number = secrets.randbelow(100)
    return secrets.choice([f"{first}_{second}_{number:02d}", f"{first}{second.capitalize()}{number:02d}",
                           f"xX_{first}{second}_Xx", f"{first}.{second}{number}", f"the_{first}_{second}"])


class TooManyTries(ValueError):
    """Logging in to an account is paused for a while after too many wrong passwords."""


_DUMMY_HASH = generate_password_hash("not a real password")


class Tripwire:
    def __init__(self, db_url=None):
        """db_url: a SQLite file path or a Postgres address (see db.py)."""
        self.conn = db.connect(db_url)

    # -- accounts -----------------------------------------------------------

    def _new_account(self, email, password):
        """Check a new account's email and password. Returns (email, password hash)."""
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("That doesn't look like an email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
        if self.conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("There's already an account with that email. Try logging in.")
        return email, generate_password_hash(password)

    def create_user(self, email, password):
        email, password_hash = self._new_account(email, password)
        return self.conn.insert("INSERT INTO users (email, password_hash, created_at, handle) VALUES (?, ?, ?, ?)",
                                (email, password_hash, db.now(), self.free_handle()))

    def free_handle(self):
        """A random old-internet name nobody else has."""
        for _ in range(50):
            handle = new_handle()
            if not self.conn.execute("SELECT id FROM users WHERE handle = ?", (handle,)).fetchone():
                return handle
        return f"{new_handle()}_{secrets.token_hex(2)}"

    def handle_of(self, user):
        """Everyone gets one; accounts made before names existed get theirs the first time it's needed."""
        if user["handle"]:
            return user["handle"]
        handle = self.free_handle()
        self.conn.execute("UPDATE users SET handle = ? WHERE id = ? AND handle IS NULL", (handle, user["id"]))
        return self.user(user["id"])["handle"]

    LOGIN_TRIES = 5            # wrong passwords in a row before an account's log-in pauses
    LOGIN_PAUSE_MINUTES = 15

    def log_in(self, email, password):
        """Returns the user if the email and password match, otherwise None. After
        LOGIN_TRIES wrong passwords, the account's log-in pauses (even for the right
        password) so a bot can't keep guessing: raises TooManyTries."""
        user = self.conn.execute("SELECT * FROM users WHERE email = ? AND guest = 0",
                                 (email.strip().lower(),)).fetchone()
        if not user:
            check_password_hash(_DUMMY_HASH, password)  # same time taken, so nobody learns which emails exist
            return None
        recent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - self.LOGIN_PAUSE_MINUTES * 60))
        recently_failed = bool(user["failed_login_at"]) and user["failed_login_at"] >= recent
        if recently_failed and user["failed_logins"] >= self.LOGIN_TRIES:
            raise TooManyTries(f"Too many wrong passwords for this account, so Tripwire has paused logging in to it "
                               f"for {self.LOGIN_PAUSE_MINUTES} minutes to keep it safe. Please try again later.")
        if check_password_hash(user["password_hash"], password):
            if user["failed_logins"]:
                self.conn.execute("UPDATE users SET failed_logins = 0, failed_login_at = NULL WHERE id = ?", (user["id"],))
            return user
        self.conn.execute("UPDATE users SET failed_logins = ?, failed_login_at = ? WHERE id = ?",
                          ((user["failed_logins"] if recently_failed else 0) + 1, db.now(), user["id"]))
        return None

    # -- confirming an email address -----------------------------------------

    CONFIRM_RESEND_MINUTES = 10  # so nobody can use Tripwire to flood a stranger's inbox

    def can_send_confirmation(self, user):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - self.CONFIRM_RESEND_MINUTES * 60))
        return not user["confirm_sent_at"] or user["confirm_sent_at"] < since

    def mark_confirmation_sent(self, user_id):
        self.conn.execute("UPDATE users SET confirm_sent_at = ? WHERE id = ?", (db.now(), user_id))

    def confirm_email(self, user_id, email):
        """True if this is still the account's address (and it's now confirmed)."""
        user = self.user(user_id)
        if not user or user["email"] != email or user["guest"]:
            return False
        if not user["email_confirmed_at"]:
            self.conn.execute("UPDATE users SET email_confirmed_at = ? WHERE id = ?", (db.now(), user_id))
        return True

    # -- guests: trying Tripwire without an account --------------------------

    GUEST_DAYS = 3  # a guest's things are deleted after this long (their visit ends when the browser closes)

    def create_guest(self):
        """A visitor who added something without an account. Nobody can log in as a guest:
        the made-up address can't receive email and '!' never matches a password."""
        return self.conn.insert("""INSERT INTO users (email, password_hash, created_at, guest, weekly_email, summary_every, handle)
                                   VALUES (?, '!', ?, 1, 0, 'off', ?)""",
                                (f"guest-{secrets.token_hex(8)}@guest.invalid", db.now(), self.free_handle()))

    def claim_guest(self, guest_id, email, password):
        """Turn a guest into a real account, keeping everything they added."""
        email, password_hash = self._new_account(email, password)
        self.conn.execute("""UPDATE users SET email = ?, password_hash = ?, guest = 0, weekly_email = 1,
                             summary_every = 'weekly', created_at = ? WHERE id = ? AND guest = 1""",
                          (email, password_hash, db.now(), guest_id))
        return guest_id

    def merge_guest(self, guest_id, user_id):
        """Someone added things as a guest, then logged in: move those things into their account."""
        guest = self.user(guest_id)
        if not guest or not guest["guest"] or guest_id == user_id:
            return
        for theme in self.conn.execute("SELECT * FROM themes WHERE user_id = ?", (guest_id,)).fetchall():
            if theme["loose"] == 1:
                target = self.single_links(user_id)
            elif theme["loose"] == 2:
                target = self.videos_list(user_id)
            else:
                same = self.conn.execute("SELECT id FROM themes WHERE user_id = ? AND loose = 0 AND LOWER(name) = ?",
                                         (user_id, theme["name"].lower())).fetchone()
                count = self.conn.execute("SELECT COUNT(*) AS n FROM themes WHERE user_id = ? AND loose = 0",
                                          (user_id,)).fetchone()["n"]
                if same:
                    target = same["id"]
                elif count < MAX_THEMES:
                    self.conn.execute("UPDATE themes SET user_id = ? WHERE id = ?", (user_id, theme["id"]))
                    continue
                else:
                    target = self.single_links(user_id)  # no room for another theme: keep the links anyway
            self.conn.execute("UPDATE watches SET theme_id = ? WHERE theme_id = ?", (target, theme["id"]))
        for table in ("book_comparisons", "book_jobs", "archive_copies", "feedback", "shelf_items", "before_checks"):
            self.conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (user_id, guest_id))
        self.conn.execute("DELETE FROM users WHERE id = ?", (guest_id,))

    def guests_this_hour(self):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))
        return self.conn.execute("SELECT COUNT(*) AS n FROM users WHERE guest = 1 AND created_at >= ?",
                                 (since,)).fetchone()["n"]

    def delete_user(self, user_id):
        """Delete an account and everything in it (the database removes their things with it;
        feedback stays, with no name attached)."""
        self.conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def delete_old_guests(self):
        """Tidy up: guests' things are deleted a few days after their visit."""
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - self.GUEST_DAYS * 86400))
        self.conn.execute("DELETE FROM users WHERE guest = 1 AND created_at < ?", (cutoff,))

    def user(self, user_id):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # -- ownership: people can only see and change their own things --------

    def own_theme(self, user_id, theme_id):
        return self.conn.execute("SELECT * FROM themes WHERE id = ? AND user_id = ?",
                                 (theme_id, user_id)).fetchone()

    def own_watch(self, user_id, watch_id):
        return self.conn.execute("""SELECT w.* FROM watches w JOIN themes t ON t.id = w.theme_id
                                    WHERE w.id = ? AND t.user_id = ?""", (watch_id, user_id)).fetchone()

    def own_entry(self, user_id, entry_id):
        return self.conn.execute("""SELECT e.*, w.label, w.kind, w.target FROM entries e
                                    JOIN watches w ON w.id = e.watch_id JOIN themes t ON t.id = w.theme_id
                                    WHERE e.id = ? AND t.user_id = ?""", (entry_id, user_id)).fetchone()

    # -- themes -------------------------------------------------------------

    def single_links(self, user_id):
        """The person's list of links watched on their own, outside any theme.
        Made the first time it's needed; it doesn't count towards MAX_THEMES."""
        row = self.conn.execute("SELECT id FROM themes WHERE user_id = ? AND loose = 1", (user_id,)).fetchone()
        if row:
            return row["id"]
        return self.conn.insert("INSERT INTO themes (user_id, name, loose, created_at) VALUES (?, ?, 1, ?)",
                                (user_id, SINGLE_LINKS, db.now()))

    def videos_list(self, user_id):
        """The person's list of watched videos (YouTube, TikTok). Like Single
        links, it's made when first needed and isn't one of their themes."""
        row = self.conn.execute("SELECT id FROM themes WHERE user_id = ? AND loose = 2", (user_id,)).fetchone()
        if row:
            return row["id"]
        return self.conn.insert("INSERT INTO themes (user_id, name, loose, created_at) VALUES (?, ?, 2, ?)",
                                (user_id, VIDEOS, db.now()))

    def videos(self, user_id):
        return self.conn.execute("""SELECT w.* FROM watches w JOIN themes t ON t.id = w.theme_id
                                    WHERE t.user_id = ? AND w.kind = 'video' ORDER BY w.id DESC""",
                                 (user_id,)).fetchall()

    def add_video(self, user_id, url):
        url = url.strip()
        if not sources.video_platform(url):
            raise ValueError("Paste a YouTube or TikTok video link.")
        state = sources.video_status(url)
        return self.conn.insert(
            """INSERT INTO watches (theme_id, kind, label, lang, target, baseline_ref, baseline_text,
                   baseline_at, last_status, created_at)
               VALUES (?, 'video', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (self.videos_list(user_id), state["title"] or state["url"], state["platform"], state["url"],
             json.dumps(state), sources.video_text(state), db.now(),
             "Watching" if state["available"] else "Unavailable", db.now()))

    def add_theme(self, user_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Give the theme a name.")
        count = self.conn.execute("SELECT COUNT(*) AS n FROM themes WHERE user_id = ? AND loose = 0",
                                  (user_id,)).fetchone()["n"]
        if count >= MAX_THEMES:
            raise ValueError(f"You can have up to {MAX_THEMES} themes. Remove one to add another.")
        return self.conn.insert("INSERT INTO themes (user_id, name, created_at) VALUES (?, ?, ?)",
                                (user_id, name, db.now()))

    def remove_theme(self, theme_id):
        self.conn.execute("DELETE FROM themes WHERE id = ?", (theme_id,))

    # -- watches ------------------------------------------------------------

    def add_watch(self, theme_id, target, kind=None, days_ago=0):
        """Start watching a Wikipedia article or a web page.

        kind: 'wikipedia' or 'web'. If not given, it's worked out from the link.
        days_ago: for Wikipedia only, start from the version of N days ago, so
                  you immediately see what changed since then.
        """
        target = target.strip()
        if not target:
            raise ValueError("Type an article name or paste a link.")
        if kind is None:
            kind = "wikipedia" if (sources.is_wikipedia_link(target) or "://" not in target) else "web"

        if kind == "wikipedia":
            lang, title = sources.parse_wikipedia_target(target)
            wiki = sources.Wikipedia(lang)
            rev = wiki.revision_at(title, days_ago=days_ago)
            row = dict(kind="wikipedia", lang=lang, target=rev["title"],
                       label=rev["title"] if lang == "en" else f"{rev['title']} ({lang} Wikipedia)",
                       baseline_ref=str(rev["revid"]), baseline_text=wiki.text_of(rev["revid"]),
                       baseline_at=rev["timestamp"])
        else:
            if "://" not in target:
                target = "https://" + target
            page = sources.fetch_page(target)
            if page["status"] == "removed":
                raise sources.SourceError("That page doesn't exist (it answered 'not found').")
            parsed = urlparse(target)
            row = dict(kind="web", lang=None, target=target,
                       label=(parsed.netloc + parsed.path).rstrip("/"),
                       baseline_ref=None, baseline_text=live.steady_text(page["text"], limit=None), baseline_at=db.now())

        return self.conn.insert(
            """INSERT INTO watches (theme_id, kind, label, lang, target, baseline_ref,
                   baseline_text, baseline_at, last_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Watching', ?)""",
            (theme_id, row["kind"], row["label"], row["lang"], row["target"], row["baseline_ref"],
             row["baseline_text"], row["baseline_at"], db.now()))

    def remove_watch(self, watch_id):
        self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    # -- checking -----------------------------------------------------------

    def check_all(self, user_id=None, deadline=None):
        """Check every watch (or just one person's). Stops early once `deadline`
        (a time.monotonic() value) passes; the oldest-checked go first, so the
        rest get their turn next time. Returns the ids of new archive entries."""
        query = """SELECT w.* FROM watches w JOIN themes t ON t.id = w.theme_id"""
        params = ()
        if user_id is not None:
            query += " WHERE t.user_id = ?"
            params = (user_id,)
        else:  # the daily run: guests' things are only checked when they ask
            query += " JOIN users u ON u.id = t.user_id WHERE u.guest = 0"
        query += " ORDER BY w.last_checked_at IS NOT NULL, w.last_checked_at, w.id"
        new_entries = []
        for watch in self.conn.execute(query, params).fetchall():
            if deadline and time.monotonic() > deadline:
                break
            entry_id = self.check_watch(watch)
            if entry_id:
                new_entries.append(entry_id)
        return new_entries

    def check_watch(self, watch):
        """Check one watch. Returns the new archive entry's id, or None if nothing changed."""
        if isinstance(watch, int):
            watch = self.conn.execute("SELECT * FROM watches WHERE id = ?", (watch,)).fetchone()
        try:
            if watch["kind"] == "wikipedia":
                return self._check_wikipedia(watch)
            if watch["kind"] == "video":
                return self._check_video(watch)
            return self._check_web(watch)
        except sources.SourceError as exc:
            self._set_status(watch["id"], f"Couldn't check: {exc}")
            return None

    def _check_wikipedia(self, watch):
        wiki = sources.Wikipedia(watch["lang"] or "en")
        revisions = wiki.revisions_since(watch["target"], int(watch["baseline_ref"]))
        if revisions is None:
            return self._record_removal(watch, "Article deleted from Wikipedia",
                                        before_link=wiki.revision_url(watch["baseline_ref"]))
        if not revisions:
            self._set_status(watch["id"], "No changes")
            return None

        latest = revisions[-1]
        new_text = wiki.text_of(latest["revid"])
        editors = list(dict.fromkeys(r.get("user", "(hidden)") for r in revisions))
        notes = [r["comment"] for r in revisions if r.get("comment")]
        return self._record_change(
            watch, new_text, new_ref=str(latest["revid"]), new_at=latest["timestamp"],
            compare_url=wiki.diff_url(watch["baseline_ref"], latest["revid"]),
            extra={"edits": len(revisions), "editors": editors, "edit_notes": notes[:10],
                   "before_link": wiki.revision_url(watch["baseline_ref"]),
                   "after_link": wiki.revision_url(latest["revid"])})

    def _check_web(self, watch):
        page = sources.fetch_page(watch["target"])
        if page["status"] == "removed":
            return self._record_removal(watch, f"Page removed (answered {page['code']} not found)")
        # Leave out bits that change on every visit ("updated 5 minutes ago"), so they're never reported as changes.
        text = live.steady_text(page["text"], limit=None)
        if text == live.steady_text(watch["baseline_text"] or "", limit=None):
            self._set_status(watch["id"], "No changes")
            return None
        return self._record_change(watch, text, new_ref=None, new_at=db.now(), compare_url=None, extra={})

    # How serious each kind of video change is, most serious first.
    VIDEO_SEVERITY = ["video_gone", "video_restricted", "video_retitled", "edit"]

    def _check_video(self, watch):
        old = json.loads(watch["baseline_ref"])
        new = sources.video_status(watch["target"])
        events = []  # (category, what happened in words)
        if old["available"] and not new["available"]:
            events.append(("video_gone", f"No longer available: {new['reason']}"))
        elif not old["available"] and new["available"]:
            events.append(("edit", "Available again"))
        if old["restricted"] is False and new["restricted"] is True:
            events.append(("video_restricted", "Now age-restricted or marked sensitive"))
        elif old["restricted"] is True and new["restricted"] is False:
            events.append(("edit", "No longer age-restricted"))
        if old["title"] and new["title"] and old["title"] != new["title"]:
            events.append(("video_retitled", f"Title changed: “{old['title']}” → “{new['title']}”"))
        if old["author"] and new["author"] and old["author"] != new["author"]:
            events.append(("edit", f"Channel name changed: “{old['author']}” → “{new['author']}”"))
        if old["unlisted"] is not None and new["unlisted"] is not None and old["unlisted"] != new["unlisted"]:
            events.append(("edit", "Now unlisted (hidden from search)" if new["unlisted"] else "Now public in search"))

        # Keep anything the platform didn't say this time (e.g. a busy page) from the last check.
        for key in ("title", "author", "restricted", "unlisted"):
            if new[key] is None:
                new[key] = old[key]
        status = "Watching" if new["available"] else "Unavailable"
        entry_id = None
        if events:
            category = min((c for c, _ in events), key=self.VIDEO_SEVERITY.index)
            details = {"events": [text for _, text in events], "video_link": watch["target"]}
            entry_id = self.conn.insert(
                """INSERT INTO entries (watch_id, detected_at, category, summary, before_text, after_text,
                       before_at, after_at, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (watch["id"], db.now(), category, " · ".join(text for _, text in events),
                 watch["baseline_text"], sources.video_text(new), watch["baseline_at"], db.now(),
                 json.dumps(details)))
            status = "Changed: see Changes" if new["available"] else "Unavailable"
        self.conn.execute(
            """UPDATE watches SET label = ?, baseline_ref = ?, baseline_text = ?, baseline_at = ?,
                   last_checked_at = ?, last_status = ? WHERE id = ?""",
            (new["title"] or watch["label"], json.dumps(new), sources.video_text(new),
             db.now() if events else watch["baseline_at"], db.now(),
             status if events else ("No changes" if new["available"] else "Unavailable"), watch["id"]))
        return entry_id

    def _record_change(self, watch, new_text, new_ref, new_at, compare_url, extra):
        # Long pages (a book at a link, say) are lined up sentence by sentence first: much faster, same result.
        long_page = len(new_text.split()) + len((watch["baseline_text"] or "").split()) > LONG_PAGE_WORDS
        result = (changes.compare_long if long_page else changes.compare)(watch["baseline_text"], new_text)
        entry_id = None
        if result["hunks"]:  # the readable text actually changed
            details = dict(extra, hunks=result["hunks"][:50], hunk_count=len(result["hunks"]),
                           name_changes=result["name_changes"],
                           spelling_changes=result["spelling_changes"])
            entry_id = self.conn.insert(
                """INSERT INTO entries (watch_id, detected_at, category, summary, words_added,
                       words_removed, before_text, after_text, before_at, after_at,
                       before_archive_url, compare_url, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (watch["id"], db.now(), result["category"], changes.describe(result),
                 result["words_added"], result["words_removed"], watch["baseline_text"],
                 new_text, watch["baseline_at"], new_at, watch["baseline_archive_url"],
                 compare_url, json.dumps(details)))

        # What we just saw becomes the new baseline for next time.
        self.conn.execute(
            """UPDATE watches SET baseline_ref = ?, baseline_text = ?, baseline_at = ?,
                   baseline_archive_url = NULL, last_checked_at = ?, last_status = ?
               WHERE id = ?""",
            (new_ref, new_text, new_at, db.now(),
             "Changed: see Changes" if entry_id else "No changes to the text", watch["id"]))
        return entry_id

    def _record_removal(self, watch, summary, before_link=None):
        if watch["last_status"] == "Removed":
            self._set_status(watch["id"], "Removed")
            return None  # already recorded last time
        before_archive = watch["baseline_archive_url"]
        details = {"before_link": before_link} if before_link else {}
        entry_id = self.conn.insert(
            """INSERT INTO entries (watch_id, detected_at, category, summary, words_removed,
                   before_text, before_at, before_archive_url, details)
               VALUES (?, ?, 'page_removed', ?, ?, ?, ?, ?, ?)""",
            (watch["id"], db.now(), summary, len(changes.tokenize(watch["baseline_text"])),
             watch["baseline_text"], watch["baseline_at"], before_archive, json.dumps(details)))
        self._set_status(watch["id"], "Removed")
        return entry_id

    def _set_status(self, watch_id, status):
        self.conn.execute("UPDATE watches SET last_checked_at = ?, last_status = ? WHERE id = ?",
                          (db.now(), status, watch_id))

    # -- public Internet Archive copies (only ever made when a person asks, twice) --

    def copy_options(self, entry=None, watch=None):
        """What could be saved to the Internet Archive for an archive entry or a
        watched item, as a list of {"label", "url"}. Empty when nothing can be
        saved (e.g. the page is already gone)."""
        if entry is not None:
            if entry["category"] not in COPY_WORTHY or entry["category"] == "page_removed":
                return []
            d = db.entry_details(entry)
            if entry["kind"] == "wikipedia":
                return [{"label": f"{entry['label']}: version before the change ({(entry['before_at'] or '')[:10]})",
                         "url": d["before_link"]},
                        {"label": f"{entry['label']}: version after the change ({(entry['after_at'] or '')[:10]})",
                         "url": d["after_link"]}]
            return [{"label": f"{entry['label']}: as it is right now", "url": entry["target"]}]
        if watch["last_status"] == "Removed" or watch["kind"] == "video":
            return []
        if watch["kind"] == "wikipedia":
            wiki = sources.Wikipedia(watch["lang"] or "en")
            return [{"label": f"{watch['label']}: version of {(watch['baseline_at'] or '')[:10]}",
                     "url": wiki.revision_url(watch["baseline_ref"])}]
        return [{"label": f"{watch['label']}: as it is right now", "url": watch["target"]}]

    def save_public_copy(self, user_id, option, watch_id=None, entry_id=None):
        """Ask the Internet Archive to keep a public, permanent copy. Only call
        this after the person has confirmed. Returns the copy's link."""
        link = sources.archive_save(option["url"], timeout=90)
        if not link:
            raise sources.SourceError("The Internet Archive didn't say where it saved the copy. Try again later.")
        self.conn.insert(
            """INSERT INTO archive_copies (user_id, watch_id, entry_id, label, page_url, archive_url, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, watch_id, entry_id, option["label"], option["url"], link, db.now()))
        return link

    def copies(self, user_id, entry_id=None, watch_id=None):
        query, params = "SELECT * FROM archive_copies WHERE user_id = ?", [user_id]
        if entry_id is not None:
            query += " AND entry_id = ?"
            params.append(entry_id)
        if watch_id is not None:
            query += " AND watch_id = ?"
            params.append(watch_id)
        return self.conn.execute(query + " ORDER BY created_at DESC, id DESC", params).fetchall()

    # -- books (only compared when a person asks) ---------------------------

    def compare_books(self, user_id, title, edition_a, edition_b):
        """Compare two editions, each given as (filename, file contents).
        Saves and returns the comparison's id. The books themselves aren't kept."""
        text_a, info_a = books.read_book(*edition_a)
        text_b, info_b = books.read_book(*edition_b)
        result = changes.compare_long(text_a, text_b)
        label_a = books.edition_label(books.clean_name(edition_a[0]), info_a)
        label_b = books.edition_label(books.clean_name(edition_b[0]), info_b)
        swaps = changes.swap_counts(result["replacements"])
        if not result["hunks"]:
            summary = "No differences in the words: the two editions read the same."
        else:
            summary = changes.describe(result)
            if swaps:
                summary += f" · {len(result['replacements'])} word swaps"
        for info in (info_a, info_b):
            facts = sources.open_library_facts(info.get("title"), info.get("authors"), info.get("isbn"))
            if facts:
                info["open_library"] = facts
        details = {"swaps": swaps, "hunks": result["hunks"][:300], "hunk_count": len(result["hunks"]),
                   "kinds": changes.kind_counts(result["hunks"]),
                   "name_changes": result["name_changes"], "info_a": info_a, "info_b": info_b,
                   "rewritten_blocks": result["rewritten_blocks"]}
        title = title.strip() or books.clean_name(edition_b[0])
        return self.conn.insert(
            """INSERT INTO book_comparisons (user_id, title, edition_a, edition_b, category, summary, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, title[:200], label_a, label_b, result["category"], summary, json.dumps(details), db.now()))

    def start_book_job(self, user_id, title, edition_a, edition_b):
        """Queue a comparison. Each edition is {"name": ..., "data": bytes} for an
        upload, or {"link": ...}. Returns the job id; run_book_job() does the work."""
        for which, edition in (("A", edition_a), ("B", edition_b)):
            if not (edition.get("data") or edition.get("link")):
                raise books.BookError(f"Add edition {which}: upload a file or paste a link.")
        return self.conn.insert(
            """INSERT INTO book_jobs (user_id, title, status, name_a, file_a, link_a, name_b, file_b, link_b, created_at)
               VALUES (?, ?, 'waiting', ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, title.strip(), edition_a.get("name"), edition_a.get("data"), edition_a.get("link"),
             edition_b.get("name"), edition_b.get("data"), edition_b.get("link"), db.now()))

    def own_book_job(self, user_id, job_id):
        return self.conn.execute("SELECT * FROM book_jobs WHERE id = ? AND user_id = ?", (job_id, user_id)).fetchone()

    def run_book_job(self, job_id):
        """Do a queued comparison. Safe to call more than once at the same time:
        only one call does the work. Returns the job as it ends up."""
        stuck = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - BOOK_JOB_STUCK_MINUTES * 60))
        claimed = self.conn.execute(
            """UPDATE book_jobs SET status = 'running', started_at = ?
               WHERE id = ? AND (status = 'waiting' OR (status = 'running' AND started_at < ?)) RETURNING id""",
            (db.now(), job_id, stuck)).fetchone()
        if claimed:
            job = self.conn.execute("SELECT * FROM book_jobs WHERE id = ?", (job_id,)).fetchone()
            try:
                editions = []
                for which in ("a", "b"):
                    data, name = job[f"file_{which}"], job[f"name_{which}"]
                    if data is None and job[f"files_deleted_at"]:
                        raise books.BookError("The book files were deleted, so this can't be compared again.")
                    if data is None:
                        name, data = books.fetch_book(job[f"link_{which}"])
                        self.conn.execute(f"UPDATE book_jobs SET name_{which} = ?, file_{which} = ? WHERE id = ?",
                                          (name, data, job_id))
                    editions.append((name, bytes(data)))
                comparison = self.compare_books(job["user_id"], job["title"], editions[0], editions[1])
                self.conn.execute("""UPDATE book_jobs SET status = 'done', comparison_id = ?, finished_at = ?
                                     WHERE id = ?""", (comparison, db.now(), job_id))
            except (books.BookError, sources.SourceError) as exc:
                self.conn.execute("UPDATE book_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                  (str(exc), db.now(), job_id))
            except Exception:
                logging.exception("Book comparison %s failed", job_id)
                self.conn.execute("UPDATE book_jobs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                                  ("Something unexpected went wrong reading these books. Try again, or try "
                                   "different files.", db.now(), job_id))
        return self.conn.execute("SELECT id, user_id, status, error, comparison_id FROM book_jobs WHERE id = ?",
                                 (job_id,)).fetchone()

    def unfinished_book_jobs(self, user_id=None):
        stuck = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - BOOK_JOB_STUCK_MINUTES * 60))
        query = """SELECT id, user_id, title, status, error, name_a, link_a, name_b, link_b, created_at
                   FROM book_jobs WHERE (status = 'waiting' OR (status = 'running' AND started_at < ?))"""
        params = [stuck]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        return self.conn.execute(query + " ORDER BY id", params).fetchall()

    def book_jobs_in_progress(self, user_id):
        """Comparisons that are waiting, running or failed, to show on the Books page."""
        return self.conn.execute("""SELECT id, title, status, error, name_a, link_a, name_b, link_b, created_at
                                    FROM book_jobs WHERE user_id = ? AND status != 'done' ORDER BY id DESC""",
                                 (user_id,)).fetchall()

    def book_job_for(self, comparison_id):
        return self.conn.execute("""SELECT id, created_at, files_deleted_at FROM book_jobs
                                    WHERE comparison_id = ?""", (comparison_id,)).fetchone()

    def delete_book_files(self, job_id=None, older_than_days=None):
        """Delete stored book files: one job's now, or every job's older than N days.
        The comparison results are kept."""
        query = "UPDATE book_jobs SET file_a = NULL, file_b = NULL, files_deleted_at = ? WHERE files_deleted_at IS NULL"
        params = [db.now()]
        if job_id is not None:
            query += " AND id = ?"
            params.append(job_id)
        if older_than_days is not None:
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - older_than_days * 86400))
            query += " AND created_at < ?"
            params.append(cutoff)
        self.conn.execute(query, params)

    def book_comparisons(self, user_id):
        return self.conn.execute("SELECT * FROM book_comparisons WHERE user_id = ? ORDER BY id DESC",
                                 (user_id,)).fetchall()

    def own_book_comparison(self, user_id, comparison_id):
        return self.conn.execute("SELECT * FROM book_comparisons WHERE id = ? AND user_id = ?",
                                 (comparison_id, user_id)).fetchone()

    def delete_book_comparison(self, comparison_id):
        self.conn.execute("DELETE FROM book_jobs WHERE comparison_id = ?", (comparison_id,))
        self.conn.execute("DELETE FROM book_comparisons WHERE id = ?", (comparison_id,))

    # -- the archive --------------------------------------------------------

    def keep(self, entry_id):
        self.conn.execute("UPDATE entries SET kept = 1 WHERE id = ?", (entry_id,))

    def disregard(self, entry_id):
        """Delete an archive entry the person doesn't care about."""
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    # -- summary emails: daily, weekly or monthly ------------------------------

    # How often, and the shortest gap between two emails (with slack, so a late daily run never skips one).
    SUMMARY_CHOICES = {"daily": ("Daily", 1, 20 / 24), "weekly": ("Weekly", 7, 6), "monthly": ("Monthly", 30, 27)}

    @staticmethod
    def summary_every(user):
        """'daily', 'weekly', 'monthly' or 'off'. Older accounts only had a weekly on/off switch."""
        return user["summary_every"] or ("weekly" if user["weekly_email"] else "off")

    def set_summary_every(self, user_id, every):
        if every not in self.SUMMARY_CHOICES and every != "off":
            raise ValueError("Choose daily, weekly, monthly or off.")
        self.conn.execute("UPDATE users SET summary_every = ?, weekly_email = ? WHERE id = ?",
                          (every, 0 if every == "off" else 1, user_id))

    def set_weekly_email(self, user_id, on):
        self.set_summary_every(user_id, "weekly" if on else "off")

    def users_due_summary(self):
        """Who gets an email today: daily ones every day, weekly ones on Mondays,
        monthly ones on the 1st (all times UTC)."""
        today = time.gmtime()
        due = []
        for user in self.conn.execute("""SELECT * FROM users WHERE guest = 0 AND email_confirmed_at IS NOT NULL
                                         ORDER BY id""").fetchall():
            every = self.summary_every(user)
            if every == "off" or (every == "weekly" and today.tm_wday != 0) or (every == "monthly" and today.tm_mday != 1):
                continue
            gap = self.SUMMARY_CHOICES[every][2]
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - gap * 86400))
            if not user["summary_sent_at"] or user["summary_sent_at"] < cutoff:
                due.append(user)
        return due

    def mark_summary_sent(self, user_id):
        self.conn.execute("UPDATE users SET summary_sent_at = ? WHERE id = ?", (db.now(), user_id))

    # -- feedback from people trying Tripwire --------------------------------

    def add_feedback(self, tried, message, signed_as=None, sealed=None):
        """Feedback is anonymous: who sent it is never stored, unless they chose to sign it with
        their username. Once the owner has locked the feedback box, it arrives `sealed` (locked in
        the sender's browser to the owner's key) and nothing readable is stored at all."""
        if sealed:
            return self.conn.insert("""INSERT INTO feedback (tried, message, sealed, signed_as, created_at)
                                       VALUES ('', '', ?, ?, ?)""", (sealed, signed_as, db.now()))
        message = (message or "").strip()
        if len(message) < 3:
            raise ValueError("Write a few words first.")
        return self.conn.insert("INSERT INTO feedback (tried, message, signed_as, created_at) VALUES (?, ?, ?, ?)",
                                ((tried or "").strip()[:60], message[:4000], signed_as, db.now()))

    def all_feedback(self):
        return self.conn.execute("SELECT id, tried, message, sealed, signed_as, created_at FROM feedback ORDER BY id DESC").fetchall()

    def lock_feedback(self, feedback_id, sealed):
        """Replace readable feedback with its locked version (and forget who sent it)."""
        self.conn.execute("""UPDATE feedback SET tried = '', message = '', sealed = ?, user_id = NULL
                             WHERE id = ? AND sealed IS NULL""", (sealed, feedback_id))

    # -- small site-wide settings -----------------------------------------------

    def setting(self, name):
        row = self.conn.execute("SELECT value FROM settings WHERE name = ?", (name,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, name, value):
        self.conn.execute("DELETE FROM settings WHERE name = ?", (name,))
        if value is not None:
            self.conn.execute("INSERT INTO settings (name, value) VALUES (?, ?)", (name, value))

    # -- demo data: real examples to explore (only ever added to one person's own account) --

    DEMO_THEMES = {
        "Indigenous communities": ["Adivasi", "Warli", "Siddi", "Basotho", "Indigenous peoples"],
        "Language & memory": ["Konkani language", "Sotho language", "Endangered language", "Oral tradition"],
    }
    DEMO_SINGLE_LINKS = ["Internet Archive"]
    DEMO_VIDEO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo", the first YouTube video (2005)
    DEMO_BOOK = ("Frankenstein: the 1818 edition vs the 1831 rewrite",
                 "https://www.gutenberg.org/cache/epub/41445/pg41445.txt",   # 1818 edition
                 "https://www.gutenberg.org/cache/epub/42324/pg42324.txt")   # 1831 edition
    DEMO_LOOK_BACK_DAYS = 90

    def add_demo_data(self, user_id, deadline=None):
        """Add the demo examples to this person's account, skipping anything already there.
        Stops adding when `deadline` (time.monotonic()) passes. Returns a report."""
        report = {"watched": 0, "changes": 0, "video": False, "book_job": None, "problems": [], "unfinished": False}

        def out_of_time():
            if deadline and time.monotonic() > deadline:
                report["unfinished"] = True
                return True
            return False

        mine = {(w["target"] or "").lower() for w in self.conn.execute(
            "SELECT w.target FROM watches w JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ?", (user_id,)).fetchall()}
        themes = {t["name"]: t["id"] for t in self.conn.execute(
            "SELECT id, name FROM themes WHERE user_id = ? AND loose = 0", (user_id,)).fetchall()}
        plan = []
        for name, titles in self.DEMO_THEMES.items():
            if name not in themes:
                try:
                    themes[name] = self.add_theme(user_id, name)
                except ValueError:  # already has 3 themes: use single links instead
                    themes[name] = self.single_links(user_id)
            plan += [(themes[name], title) for title in titles]
        plan += [(self.single_links(user_id), title) for title in self.DEMO_SINGLE_LINKS]

        new_watches = []
        for theme_id, title in plan:
            if title.lower() in mine or out_of_time():
                continue
            try:
                new_watches.append(self.add_watch(theme_id, title, kind="wikipedia", days_ago=self.DEMO_LOOK_BACK_DAYS))
                report["watched"] += 1
            except (ValueError, sources.SourceError) as exc:
                report["problems"].append(f"{title}: {exc}")
        for watch_id in new_watches:
            if out_of_time():
                break
            if self.check_watch(watch_id):
                report["changes"] += 1

        if self.DEMO_VIDEO.lower() not in mine and not out_of_time():
            try:
                self.add_video(user_id, self.DEMO_VIDEO)
                report["video"] = True
            except (ValueError, sources.SourceError) as exc:
                report["problems"].append(f"Video: {exc}")

        title, link_a, link_b = self.DEMO_BOOK
        already = self.conn.execute("SELECT id FROM book_jobs WHERE user_id = ? AND link_a = ? AND link_b = ?",
                                    (user_id, link_a, link_b)).fetchone()
        if not already:
            report["book_job"] = self.start_book_job(user_id, title, {"link": link_a}, {"link": link_b})
        return report

    # -- totals for the About page (counts only, nothing personal) -----------

    def totals(self):
        def count(sql):
            return self.conn.execute(sql).fetchone()["n"]
        return {"people": count("SELECT COUNT(*) AS n FROM users WHERE guest = 0"),
                "watched": count("SELECT COUNT(*) AS n FROM watches"),
                "changes": count("SELECT COUNT(*) AS n FROM entries"),
                "books": count("SELECT COUNT(*) AS n FROM book_comparisons"),
                "copies": count("SELECT COUNT(*) AS n FROM archive_copies"),
                "feedback": count("SELECT COUNT(*) AS n FROM feedback")}

    # -- weekly summary -----------------------------------------------------

    def weekly_summary(self, user_id, every="weekly"):
        """The summary for the last day, week or month (`every`: 'daily', 'weekly' or 'monthly')."""
        name, days, _gap = self.SUMMARY_CHOICES.get(every, self.SUMMARY_CHOICES["weekly"])
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        rows = self.conn.execute(
            """SELECT e.*, w.label FROM entries e JOIN watches w ON w.id = e.watch_id
               JOIN themes t ON t.id = w.theme_id
               WHERE t.user_id = ? AND e.detected_at >= ? ORDER BY e.detected_at""",
            (user_id, start.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchall()
        tracked = self.conn.execute("""SELECT COUNT(*) AS n FROM watches w JOIN themes t ON t.id = w.theme_id
                                       WHERE t.user_id = ?""", (user_id,)).fetchone()["n"]
        counts = Counter(r["category"] for r in rows)
        changed_items = len({r["watch_id"] for r in rows})
        names = [(r["label"], old, new) for r in rows for old, new in db.entry_details(r).get("name_changes", [])]
        big = [r for r in rows if r["category"] in ("major", "removal", "page_removed", "video_gone", "video_restricted")]

        period = f"{start:%d %b} – {end:%d %b}"
        if not rows:
            text = f"Tripwire {name.lower()} summary ({period}): no changes across your {tracked} tracked items."
        else:
            parts = ", ".join(f"{n} {changes.CATEGORIES[c].lower()}" for c, n in counts.most_common())
            text = (f"Tripwire {name.lower()} summary ({period}): {len(rows)} change{'s' if len(rows) != 1 else ''} "
                    f"across {changed_items} of your {tracked} tracked items. {parts}.")
            if names:
                text += " Names that changed spelling: " + "; ".join(f"{o} → {n} ({label})" for label, o, n in names[:5]) + "."
            if big:
                text += " Worth a look: " + ", ".join(dict.fromkeys(r["label"] for r in big)) + "."
        copied = {r["entry_id"] for r in self.copies(user_id)}
        offer_copy = [r for r in rows if r["category"] in ("major", "removal") and r["id"] not in copied]
        return {"every": every if every in self.SUMMARY_CHOICES else "weekly", "name": name,
                "period": period, "entries": rows, "counts": counts, "changed_items": changed_items,
                "tracked": tracked, "names": names, "big": big, "offer_copy": offer_copy, "text": text}

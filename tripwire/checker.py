"""What Tripwire actually does: add things to watch, check them for changes,
save each change into the archive, and write the weekly summary."""

import json
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from werkzeug.security import check_password_hash, generate_password_hash

from . import changes, db, sources

MAX_THEMES = 3
MIN_PASSWORD = 8


class Tripwire:
    def __init__(self, db_url=None, archive=True):
        """db_url: a SQLite file path or a Postgres address (see db.py).
        archive=False turns off Internet Archive copies (used by the tests)."""
        self.archive = archive
        self.conn = db.connect(db_url)

    # -- accounts -----------------------------------------------------------

    def create_user(self, email, password):
        email = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("That doesn't look like an email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
        if self.conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
            raise ValueError("There's already an account with that email. Try logging in.")
        return self.conn.insert("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                                (email, generate_password_hash(password), db.now()))

    def log_in(self, email, password):
        """Returns the user if the email and password match, otherwise None."""
        user = self.conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            return user
        return None

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

    def add_theme(self, user_id, name):
        name = name.strip()
        if not name:
            raise ValueError("Give the theme a name.")
        count = self.conn.execute("SELECT COUNT(*) AS n FROM themes WHERE user_id = ?", (user_id,)).fetchone()["n"]
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
                       baseline_ref=None, baseline_text=page["text"], baseline_at=db.now())

        # The Internet Archive copy of this first version is saved later, by save_archive_copies().
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
        if page["text"] == watch["baseline_text"]:
            self._set_status(watch["id"], "No changes")
            return None
        return self._record_change(watch, page["text"], new_ref=None, new_at=db.now(),
                                   compare_url=None, extra={})

    def _record_change(self, watch, new_text, new_ref, new_at, compare_url, extra):
        result = changes.compare(watch["baseline_text"], new_text)
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

        # What we just saw becomes the new baseline for next time. Its Internet
        # Archive copy is saved later, by save_archive_copies().
        self.conn.execute(
            """UPDATE watches SET baseline_ref = ?, baseline_text = ?, baseline_at = ?,
                   baseline_archive_url = NULL, last_checked_at = ?, last_status = ?
               WHERE id = ?""",
            (new_ref, new_text, new_at, db.now(),
             "Changed: saved to archive" if entry_id else "No changes to the text", watch["id"]))
        return entry_id

    def _record_removal(self, watch, summary, before_link=None):
        if watch["last_status"] == "Removed":
            self._set_status(watch["id"], "Removed")
            return None  # already recorded last time
        before_archive = watch["baseline_archive_url"]
        if not before_archive and self.archive and watch["kind"] == "web":
            try:
                before_archive = sources.archive_latest(watch["target"])
            except sources.SourceError:
                pass  # still record the removal, just without the link
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

    # -- Internet Archive copies -------------------------------------------

    def save_archive_copies(self, deadline=None):
        """Ask the Internet Archive to save every version Tripwire is holding as a
        baseline but hasn't got a public copy of yet. Each save can take up to a
        minute, so this stops once `deadline` (a time.monotonic() value) passes;
        the rest are picked up next time. Returns how many copies were saved."""
        if not self.archive:
            return 0
        saved = 0
        pending = self.conn.execute("""SELECT * FROM watches WHERE baseline_archive_url IS NULL
                                       AND last_status != 'Removed' ORDER BY id""").fetchall()
        for watch in pending:
            if deadline and time.monotonic() > deadline:
                break
            if watch["kind"] == "wikipedia":
                url = sources.Wikipedia(watch["lang"] or "en").revision_url(watch["baseline_ref"])
            else:
                url = watch["target"]
            timeout = 120 if deadline is None else max(5, deadline - time.monotonic())
            try:
                link = sources.archive_save(url, timeout=timeout)
            except sources.SourceError:
                continue  # try again next time
            if not link:
                continue
            self.conn.execute("UPDATE watches SET baseline_archive_url = ? WHERE id = ? AND baseline_at = ?",
                              (link, watch["id"], watch["baseline_at"]))
            # The same version is the "after" of the change that produced it.
            self.conn.execute("""UPDATE entries SET after_archive_url = ?
                                 WHERE watch_id = ? AND after_at = ? AND after_archive_url IS NULL""",
                              (link, watch["id"], watch["baseline_at"]))
            saved += 1
        return saved

    # -- the archive --------------------------------------------------------

    def keep(self, entry_id):
        self.conn.execute("UPDATE entries SET kept = 1 WHERE id = ?", (entry_id,))

    def disregard(self, entry_id):
        """Delete an archive entry the person doesn't care about."""
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    # -- weekly summary -----------------------------------------------------

    def weekly_summary(self, user_id, days=7):
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
        big = [r for r in rows if r["category"] in ("major", "removal", "page_removed")]

        period = f"{start:%d %b} – {end:%d %b}"
        if not rows:
            text = f"Tripwire weekly summary ({period}): no changes across your {tracked} tracked items."
        else:
            parts = ", ".join(f"{n} {changes.CATEGORIES[c].lower()}" for c, n in counts.most_common())
            text = (f"Tripwire weekly summary ({period}): {len(rows)} change{'s' if len(rows) != 1 else ''} "
                    f"across {changed_items} of your {tracked} tracked items. {parts}.")
            if names:
                text += " Names that changed spelling: " + "; ".join(f"{o} → {n} ({label})" for label, o, n in names[:5]) + "."
            if big:
                text += " Worth a look: " + ", ".join(dict.fromkeys(r["label"] for r in big)) + "."
        return {"period": period, "entries": rows, "counts": counts, "changed_items": changed_items,
                "tracked": tracked, "names": names, "big": big, "text": text}

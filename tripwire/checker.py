"""What Tripwire actually does: add things to watch, check them for changes,
save each change into the archive, and write the weekly summary."""

import json
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from . import changes, db, sources

MAX_THEMES = 3


class Tripwire:
    def __init__(self, db_path=None, archive=True):
        """archive=False turns off Internet Archive copies (used by the tests)."""
        self.db_path = db_path or db.DEFAULT_PATH
        self.archive = archive
        self.conn = db.connect(self.db_path)
        self._threads = []

    # -- themes -------------------------------------------------------------

    def add_theme(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Give the theme a name.")
        count = self.conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0]
        if count >= MAX_THEMES:
            raise ValueError(f"You can have up to {MAX_THEMES} themes. Remove one to add another.")
        with self.conn:
            cur = self.conn.execute("INSERT INTO themes (name, created_at) VALUES (?, ?)", (name, db.now()))
        return cur.lastrowid

    def remove_theme(self, theme_id):
        with self.conn:
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
            archive_url = wiki.revision_url(rev["revid"])
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
            archive_url = target

        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO watches (theme_id, kind, label, lang, target, baseline_ref,
                       baseline_text, baseline_at, last_status, created_at)
                   VALUES (:theme_id, :kind, :label, :lang, :target, :baseline_ref,
                       :baseline_text, :baseline_at, 'Watching', :created_at)""",
                dict(row, theme_id=theme_id, created_at=db.now()))
        watch_id = cur.lastrowid
        self._save_to_archive(archive_url, [("watches", watch_id, "baseline_archive_url")])
        return watch_id

    def remove_watch(self, watch_id):
        with self.conn:
            self.conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))

    # -- checking -----------------------------------------------------------

    def check_all(self):
        """Check every watch. Returns the ids of new archive entries."""
        new_entries = []
        for watch in self.conn.execute("SELECT * FROM watches ORDER BY id").fetchall():
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
            archive_url=wiki.revision_url(latest["revid"]),
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
                                   compare_url=None, archive_url=watch["target"], extra={})

    def _record_change(self, watch, new_text, new_ref, new_at, compare_url, archive_url, extra):
        result = changes.compare(watch["baseline_text"], new_text)
        entry_id = None
        if result["hunks"]:  # the readable text actually changed
            details = dict(extra, hunks=result["hunks"][:50], hunk_count=len(result["hunks"]),
                           name_changes=result["name_changes"],
                           spelling_changes=result["spelling_changes"])
            with self.conn:
                cur = self.conn.execute(
                    """INSERT INTO entries (watch_id, detected_at, category, summary, words_added,
                           words_removed, before_text, after_text, before_at, after_at,
                           before_archive_url, compare_url, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (watch["id"], db.now(), result["category"], changes.describe(result),
                     result["words_added"], result["words_removed"], watch["baseline_text"],
                     new_text, watch["baseline_at"], new_at, watch["baseline_archive_url"],
                     compare_url, json.dumps(details)))
            entry_id = cur.lastrowid

        # What we just saw becomes the new baseline for next time.
        with self.conn:
            self.conn.execute(
                """UPDATE watches SET baseline_ref = ?, baseline_text = ?, baseline_at = ?,
                       baseline_archive_url = NULL, last_checked_at = ?, last_status = ?
                   WHERE id = ?""",
                (new_ref, new_text, new_at, db.now(),
                 "Changed: saved to archive" if entry_id else "No changes to the text", watch["id"]))
        targets = [("watches", watch["id"], "baseline_archive_url")]
        if entry_id:
            targets.append(("entries", entry_id, "after_archive_url"))
        self._save_to_archive(archive_url, targets)
        return entry_id

    def _record_removal(self, watch, summary, before_link=None):
        if watch["last_status"] == "Removed":
            self._set_status(watch["id"], "Removed")
            return None  # already recorded last time
        before_archive = watch["baseline_archive_url"]
        if not before_archive and self.archive and watch["kind"] == "web":
            before_archive = sources.archive_latest(watch["target"])
        details = {"before_link": before_link} if before_link else {}
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO entries (watch_id, detected_at, category, summary, words_removed,
                       before_text, before_at, before_archive_url, details)
                   VALUES (?, ?, 'page_removed', ?, ?, ?, ?, ?, ?)""",
                (watch["id"], db.now(), summary, len(changes.tokenize(watch["baseline_text"])),
                 watch["baseline_text"], watch["baseline_at"], before_archive, json.dumps(details)))
        self._set_status(watch["id"], "Removed")
        return cur.lastrowid

    def _set_status(self, watch_id, status):
        with self.conn:
            self.conn.execute("UPDATE watches SET last_checked_at = ?, last_status = ? WHERE id = ?",
                              (db.now(), status, watch_id))

    # -- Internet Archive copies (run in the background: they can take a minute) --

    def _save_to_archive(self, url, targets):
        if not self.archive:
            return
        thread = threading.Thread(target=self._archive_worker, args=(url, targets), daemon=True)
        thread.start()
        self._threads.append(thread)

    def _archive_worker(self, url, targets):
        try:
            link = sources.archive_save(url)
        except sources.SourceError:
            try:
                link = sources.archive_latest(url)
            except sources.SourceError:
                link = None
        if not link:
            return
        conn = db.connect(self.db_path)
        with conn:
            for table, row_id, column in targets:
                # Only fill in an empty slot, so a slow save never overwrites a newer one.
                conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ? AND {column} IS NULL",
                             (link, row_id))
        conn.close()

    def wait_for_archive(self, timeout=180):
        """Wait for background Internet Archive saves (used by the command line)."""
        for thread in self._threads:
            thread.join(timeout)

    # -- the archive --------------------------------------------------------

    def keep(self, entry_id):
        with self.conn:
            self.conn.execute("UPDATE entries SET kept = 1 WHERE id = ?", (entry_id,))

    def disregard(self, entry_id):
        """Delete an archive entry the person doesn't care about."""
        with self.conn:
            self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    # -- weekly summary -----------------------------------------------------

    def weekly_summary(self, days=7):
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        rows = self.conn.execute(
            """SELECT e.*, w.label FROM entries e JOIN watches w ON w.id = e.watch_id
               WHERE e.detected_at >= ? ORDER BY e.detected_at""",
            (start.strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchall()
        tracked = self.conn.execute("SELECT COUNT(*) FROM watches").fetchone()[0]
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

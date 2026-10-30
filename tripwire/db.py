"""Tripwire's storage.

Two ways to store things, same tables either way:
  - On your laptop: one small SQLite file (tripwire.db). Nothing to set up.
  - Online (Vercel): a Postgres database. Vercel forgets files between visits,
    so set the DATABASE_URL setting to your database's address and Tripwire
    uses that instead.

Tables:
  users    people with an account, and guests trying Tripwire without one
  themes   what a person cares about, e.g. "Indigenous communities"
  watches  the things being watched inside a theme (a Wikipedia article or a web page),
           with a saved copy of the version Tripwire last saw (the "baseline")
  entries  the archive: one entry per detected change, holding the before and
           after text so it survives even if the original disappears
  shelf_items    "My shelf": documents and pages a person keeps their own copy of
  before_checks  a page compared with an older copy of it (Kiwix or the Internet Archive)
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_PATH = os.environ.get("TRIPWIRE_DB", os.path.join(os.path.dirname(__file__), "..", "tripwire.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             {id},
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
    id          {id},
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    loose       INTEGER NOT NULL DEFAULT 0,      -- 1 = the "Single links" list, not a real theme
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watches (
    id                    {id},
    theme_id              INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    kind                  TEXT NOT NULL,          -- 'wikipedia' or 'web'
    label                 TEXT NOT NULL,          -- what we show people
    lang                  TEXT,                   -- Wikipedia language code, e.g. 'en', 'gom'
    target                TEXT NOT NULL,          -- article title, or page link
    baseline_ref          TEXT,                   -- Wikipedia version number of the baseline
    baseline_text         TEXT,
    baseline_at           TEXT,
    baseline_archive_url  TEXT,                   -- Internet Archive copy of the baseline
    last_checked_at       TEXT,
    last_status           TEXT,                   -- short note from the last check
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id                  {id},
    watch_id            INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    detected_at         TEXT NOT NULL,
    category            TEXT NOT NULL,           -- see changes.CATEGORIES
    summary             TEXT NOT NULL,
    words_added         INTEGER NOT NULL DEFAULT 0,
    words_removed       INTEGER NOT NULL DEFAULT 0,
    before_text         TEXT,
    after_text          TEXT,
    before_at           TEXT,
    after_at            TEXT,
    before_archive_url  TEXT,
    after_archive_url   TEXT,
    compare_url         TEXT,                    -- e.g. Wikipedia's own diff page
    details             TEXT NOT NULL DEFAULT '{{}}',  -- JSON: hunks, names, editors
    kept                INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS archive_copies (
    id           {id},
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    watch_id     INTEGER REFERENCES watches(id) ON DELETE SET NULL,
    entry_id     INTEGER REFERENCES entries(id) ON DELETE SET NULL,
    label        TEXT NOT NULL,                  -- what was saved, in words
    page_url     TEXT NOT NULL,                  -- the page that was saved
    archive_url  TEXT NOT NULL,                  -- where the Internet Archive keeps it
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_comparisons (
    id          {id},
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    edition_a   TEXT NOT NULL,                   -- e.g. "dahl-1998.epub (EPUB, 31,000 words)"
    edition_b   TEXT NOT NULL,
    category    TEXT NOT NULL,
    summary     TEXT NOT NULL,
    details     TEXT NOT NULL DEFAULT '{{}}',    -- JSON: word swaps, changes with a little context
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id          {id},
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    tried       TEXT,                            -- which part of Tripwire they tried
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_jobs (
    id               {id},
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL,              -- 'waiting', 'running', 'done' or 'failed'
    error            TEXT,
    name_a           TEXT,                       -- file name of edition A
    file_a           {blob},                     -- the book file itself: deleted after 7 days
    link_a           TEXT,                       -- or the link it comes from
    name_b           TEXT,
    file_b           {blob},
    link_b           TEXT,
    comparison_id    INTEGER REFERENCES book_comparisons(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    files_deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    name   TEXT PRIMARY KEY,                     -- e.g. 'feedback_key': the public key that locks feedback
    value  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS before_checks (
    id             {id},
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    url            TEXT NOT NULL,                -- the page as it is today
    title          TEXT NOT NULL,
    source         TEXT NOT NULL,                -- 'kiwix' or 'archive': where the older copy came from
    source_name    TEXT NOT NULL,                -- in words, e.g. "Kiwix's offline Wikipedia (January 2024)"
    before_date    TEXT,                         -- when the older copy was made
    before_link    TEXT,                         -- where to read the older copy
    download_link  TEXT,                         -- Kiwix only: the whole offline Wikipedia, to keep
    category       TEXT NOT NULL,
    summary        TEXT NOT NULL,
    details        TEXT NOT NULL DEFAULT '{{}}', -- JSON: changes with a little context
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shelf_items (
    id               {id},
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title            TEXT NOT NULL,
    note             TEXT NOT NULL DEFAULT '',
    source_url       TEXT,                       -- where it came from, if it came from a link
    file_name        TEXT,                       -- the original file, if there is one
    file_size        INTEGER NOT NULL DEFAULT 0,
    file_data        {blob},
    text             TEXT NOT NULL DEFAULT '',   -- its words, so it can be searched and read aloud
    text_from        TEXT,                       -- how the words were read, in words
    watch_id         INTEGER REFERENCES watches(id) ON DELETE SET NULL,
    before_check_id  INTEGER REFERENCES before_checks(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL
)
"""

# Columns added after Tripwire first went online. Older databases get them
# added automatically; nothing to do by hand.
ADDED_COLUMNS = [
    ("themes", "loose", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "weekly_email", "INTEGER NOT NULL DEFAULT 1"),   # 1 = send the weekly summary by email
    ("users", "summary_sent_at", "TEXT"),                       # when the last one went out
    ("users", "guest", "INTEGER NOT NULL DEFAULT 0"),           # 1 = a visitor without an account (yet)
    ("users", "summary_every", "TEXT"),                         # 'daily', 'weekly', 'monthly' or 'off';
                                                                # empty = older accounts: follow weekly_email
    ("users", "failed_logins", "INTEGER NOT NULL DEFAULT 0"),   # wrong passwords in a row (see log_in)
    ("users", "failed_login_at", "TEXT"),                       # when the last wrong one was
    ("users", "email_confirmed_at", "TEXT"),                    # no summary emails until the address is confirmed
    ("users", "confirm_sent_at", "TEXT"),                       # when the last "confirm your email" went out
    # The vault (locked shelf items). Tripwire only ever holds the key *locked*: by the person's
    # passphrase, or by their recovery code. Both stay in their browser; Tripwire never sees either.
    ("users", "vault", "TEXT"),                                 # JSON: salts, iterations and the two locked keys
    ("shelf_items", "locked", "INTEGER NOT NULL DEFAULT 0"),    # 1 = locked in the person's browser before upload
    ("shelf_items", "sealed_title", "TEXT"),                    # locked items: the title, locked
    ("shelf_items", "sealed", "TEXT"),                          # locked items: title, note, words, file name, locked
    ("users", "handle", "TEXT"),                                # a random old-internet name, shown instead of the email
    ("before_checks", "locked", "INTEGER NOT NULL DEFAULT 0"),  # 1 = locked with the person's vault
    ("before_checks", "sealed_title", "TEXT"),                  # locked: which page and the one-line summary, locked
    ("before_checks", "sealed", "TEXT"),                        # locked: the whole comparison, locked
    ("feedback", "sealed", "TEXT"),                             # locked to the site owner's key: only they can read it
    ("feedback", "signed_as", "TEXT"),                          # a username, only if the sender chose to sign
]


class SetupError(RuntimeError):
    """Something about Tripwire's settings is missing or wrong. The message says
    what to fix in plain words, so it's safe to show on the page."""


# Names Vercel and database providers commonly give the database address.
DATABASE_SETTINGS = ["DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL", "SUPABASE_DB_URL"]


def find_database_url():
    """Look for the database address under the usual names, including names
    with a custom prefix (e.g. STORAGE_DATABASE_URL). Returns (name, address)."""
    candidates = DATABASE_SETTINGS + sorted(
        name for name in os.environ
        if name.endswith(("_DATABASE_URL", "_POSTGRES_URL")) and name not in DATABASE_SETTINGS)
    for name in candidates:
        value = os.environ.get(name, "").strip()
        if value.startswith(("postgres://", "postgresql://")):
            return name, value
    return None, None


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Connection:
    """A thin wrapper so the rest of Tripwire can write SQL with ? placeholders
    and get rows back as dictionaries, whether it's SQLite or Postgres."""

    def __init__(self, url=None):
        if not url:
            _name, url = find_database_url()
        if not url and os.environ.get("VERCEL"):
            raise SetupError(
                "Tripwire can't find its database. In Vercel, open your project's Storage tab and make sure "
                "a Postgres database is connected to this project (for Production), then redeploy. "
                "Its address should appear under Settings → Environment Variables as DATABASE_URL "
                "or POSTGRES_URL.")
        url = url or DEFAULT_PATH
        self.postgres = url.startswith(("postgres://", "postgresql://"))
        if self.postgres:
            import psycopg
            from psycopg.rows import dict_row
            try:
                self.raw = psycopg.connect(url, row_factory=dict_row, connect_timeout=15)
            except psycopg.OperationalError as exc:
                raise SetupError(
                    "Tripwire found a database address but couldn't connect to it. Check the database is "
                    "running in your database provider's dashboard, and that the address in Vercel's "
                    "Environment Variables is complete and current.") from exc
            schema = SCHEMA.format(id="SERIAL PRIMARY KEY", blob="BYTEA")
        else:
            self.raw = sqlite3.connect(url, timeout=30)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute("PRAGMA foreign_keys = ON")
            schema = SCHEMA.format(id="INTEGER PRIMARY KEY", blob="BLOB")
        for statement in schema.split(";"):
            self.raw.execute(statement)
        for table, column, definition in ADDED_COLUMNS:
            if column not in self._columns(table):
                self.raw.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.raw.commit()

    def _columns(self, table):
        if self.postgres:
            # Only Tripwire's own tables: other schemas in the same database may have a "users" table too.
            rows = self.raw.execute("SELECT column_name AS name FROM information_schema.columns "
                                    "WHERE table_name = %s AND table_schema = current_schema()", (table,)).fetchall()
        else:
            rows = self.raw.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    def execute(self, sql, params=()):
        """Run one SQL statement and save it. Use .fetchone() / .fetchall() on
        the result to read rows."""
        if self.postgres:
            # psycopg uses % for its own placeholders, so a literal % (as in LIKE 'x%') is written %%.
            sql = sql.replace("%", "%%").replace("?", "%s")
        try:
            cur = self.raw.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        except Exception:
            self.raw.rollback()
            raise
        self.raw.commit()
        return Result(rows)

    def insert(self, sql, params=()):
        """Run an INSERT and return the new row's id."""
        return self.execute(sql + " RETURNING id", params).fetchone()["id"]

    def close(self):
        self.raw.close()


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


def connect(url=None):
    return Connection(url)


def entry_details(entry):
    return json.loads(entry["details"] or "{}")

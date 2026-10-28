"""Tripwire's storage: one small SQLite database file (tripwire.db).

Three tables:
  themes   what a person cares about, e.g. "Indigenous communities"
  watches  the things being watched inside a theme (a Wikipedia article or a web page),
           with a saved copy of the version Tripwire last saw (the "baseline")
  entries  the archive: one entry per detected change, holding the before and
           after text so it survives even if the original disappears
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_PATH = os.environ.get("TRIPWIRE_DB", os.path.join(os.path.dirname(__file__), "..", "tripwire.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watches (
    id                    INTEGER PRIMARY KEY,
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
    id                  INTEGER PRIMARY KEY,
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
    details             TEXT NOT NULL DEFAULT '{}',  -- JSON: hunks, names, editors
    kept                INTEGER NOT NULL DEFAULT 0
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path=None):
    conn = sqlite3.connect(path or DEFAULT_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def entry_details(entry):
    return json.loads(entry["details"] or "{}")

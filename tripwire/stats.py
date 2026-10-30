"""Numbers for the Stats page, from two outside sources. Both are remembered
(cached) in the database, so the page is quick and the sources aren't overloaded.

  Cloudflare Radar  share of web traffic made by humans vs bots, near-live.
                    Needs a free Cloudflare API token in CLOUDFLARE_API_TOKEN.
  Open Library      how many books in its catalogue were first published each
                    year, and how many of those have a digital copy. Fetched a
                    few years at a time, then kept.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db, sources

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4/radar/http"
TRAFFIC_CACHE_MINUTES = 15

FIRST_YEAR = 1900          # earlier books are counted together as "before 1900"

# A world estimate, separate from Open Library (which leans towards books held
# in Western libraries). Anchored on Google's 2010 count, then adding a yearly
# range of new titles. Books never recorded anywhere (no ISBN, no library
# record, e.g. printed locally and sold by hand) are in none of these numbers.
GOOGLE_2010_COUNT = 129_864_880
GOOGLE_2010_YEAR = 2010
NEW_TITLES_PER_YEAR_LOW = 2_200_000   # UNESCO's widely cited figure for recorded new titles
NEW_TITLES_PER_YEAR_HIGH = 4_000_000  # estimates that include self-published ISBN titles
WORLD_SOURCES = [
    ("Google Books, “Books of the world, stand up and be counted! All 129,864,880 of you” (Leonid Taycher, Aug 2010)",
     "https://www.npr.org/sections/thetwo-way/2010/08/06/129027628/google-tallies-129-864-880-titles"),
    ("Michel et al., “Quantitative Analysis of Culture Using Millions of Digitized Books”, Science (2011)",
     "https://pubmed.ncbi.nlm.nih.gov/21163965/"),
    ("UNESCO estimate of ~2.2 million new titles a year (via Books published per country per year)",
     "https://en.wikipedia.org/wiki/Books_published_per_country_per_year"),
    ("Bowker: over 2 million self-published ISBN titles a year in the US since 2019 (Publishers Weekly)",
     "https://www.publishersweekly.com/pw/by-topic/industry-news/publisher-news/article/91574-self-publishing-is-thriving-according-to-bowker-report.html"),
]
RECENT_REFRESH_DAYS = 7    # the last few years keep growing in the catalogue, so refresh them weekly


class Stats:
    def __init__(self, conn):
        self.conn = conn
        self.conn.execute("""CREATE TABLE IF NOT EXISTS stats_cache (
                                 key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL)""")

    # -- cache ---------------------------------------------------------------

    def _get(self, key, max_age=None):
        row = self.conn.execute("SELECT value, fetched_at FROM stats_cache WHERE key = ?", (key,)).fetchone()
        if row and (max_age is None or time.time() - row["fetched_at"] < max_age):
            return json.loads(row["value"])
        return None

    def _put(self, key, value):
        self.conn.execute("""INSERT INTO stats_cache (key, value, fetched_at) VALUES (?, ?, ?)
                             ON CONFLICT (key) DO UPDATE SET value = excluded.value, fetched_at = excluded.fetched_at""",
                          (key, json.dumps(value), time.time()))

    # -- humans vs bots (Cloudflare Radar) -----------------------------------

    def traffic(self):
        """{"connected", "human", "bot", "series": {"timestamps", "human", "bot"}, "updated"} or an error."""
        token = os.environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            return {"connected": False}
        cached = self._get("cloudflare_traffic", TRAFFIC_CACHE_MINUTES * 60)
        if cached:
            return cached
        headers = {"Authorization": f"Bearer {token}"}
        try:
            summary = _cloudflare(f"{CLOUDFLARE_API}/summary/bot_class", {"dateRange": "1d"}, headers)
            series = _cloudflare(f"{CLOUDFLARE_API}/timeseries_groups/bot_class",
                                 {"dateRange": "7d", "aggInterval": "1h"}, headers)
            human, bot = _human_bot(summary.get("summary_0", {}))
            serie = series.get("serie_0", {})
            result = {"connected": True, "human": human, "bot": bot,
                      "series": {"timestamps": serie.get("timestamps", []),
                                 "human": [float(v) for v in _pick(serie, "human")],
                                 "bot": [float(v) for v in _pick(serie, "bot")]},
                      "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
        except (sources.SourceError, KeyError, ValueError, TypeError) as exc:
            stale = self._get("cloudflare_traffic")
            if stale:
                return dict(stale, stale=True)
            return {"connected": True, "error": str(exc) or "Cloudflare's answer wasn't in the expected format."}
        self._put("cloudflare_traffic", result)
        return result

    # -- books published (Open Library) --------------------------------------

    def book_years_needed(self):
        """Years (and the 'before 1900' bucket) not fetched yet, or due a refresh."""
        this_year = datetime.now(timezone.utc).year
        keys = ["before"] + list(range(FIRST_YEAR, this_year))
        have = {row["key"]: row["fetched_at"] for row in
                self.conn.execute("SELECT key, fetched_at FROM stats_cache WHERE key LIKE 'books:%'").fetchall()}
        needed = []
        for year in keys:
            fetched = have.get(f"books:{year}")
            recent = year != "before" and year >= this_year - 3
            if fetched is None or (recent and time.time() - fetched > RECENT_REFRESH_DAYS * 86400):
                needed.append(year)
        return needed, len(keys)

    def fill_book_years(self, seconds=25, workers=3):
        """Fetch some missing years from Open Library, a few at a time in parallel,
        stopping after about `seconds`, or straight away if Open Library says it's
        busy (it asks to be called gently). Returns (still missing, total, problem)."""
        deadline = time.monotonic() + seconds
        needed, total = self.book_years_needed()
        problem = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            first = True
            while needed and (first or time.monotonic() < deadline):  # always make some progress
                first = False
                batch, needed = needed[:workers], needed[workers:]
                for year, result in zip(batch, pool.map(_open_library_year, batch)):
                    if isinstance(result, dict):
                        self._put(f"books:{year}", result)
                    else:
                        problem = result
                if problem and ("busy" in problem or "429" in problem):
                    break
        self._put("openlibrary_problem", problem)
        return len(self.book_years_needed()[0]), total, problem

    @staticmethod
    def world_estimate():
        """From Google's 2010 count to this year: a low-high range and its middle."""
        this_year = datetime.now(timezone.utc).year
        mid_new = (NEW_TITLES_PER_YEAR_LOW + NEW_TITLES_PER_YEAR_HIGH) // 2
        years = list(range(GOOGLE_2010_YEAR, this_year + 1))
        world = {"years": years,
                 "low": [GOOGLE_2010_COUNT + NEW_TITLES_PER_YEAR_LOW * (y - GOOGLE_2010_YEAR) for y in years],
                 "mid": [GOOGLE_2010_COUNT + mid_new * (y - GOOGLE_2010_YEAR) for y in years],
                 "high": [GOOGLE_2010_COUNT + NEW_TITLES_PER_YEAR_HIGH * (y - GOOGLE_2010_YEAR) for y in years]}
        world["now"] = {"year": this_year, "low": world["low"][-1], "mid": world["mid"][-1], "high": world["high"][-1]}
        return world

    def books(self):
        """Per-year and running totals for the chart, plus peak and lowest years."""
        rows = {row["key"][6:]: json.loads(row["value"]) for row in
                self.conn.execute("SELECT key, value FROM stats_cache WHERE key LIKE 'books:%'").fetchall()}
        before = rows.pop("before", {"all": 0, "digital": 0})
        years = sorted(int(y) for y in rows)
        if not years:
            return None
        per_year = {"all": [], "digital": [], "print": []}
        running = {"all": [], "digital": [], "print": []}
        totals = {"all": before["all"], "digital": before["digital"], "print": before["all"] - before["digital"]}
        for year in years:
            counts = rows[str(year)]
            values = {"all": counts["all"], "digital": counts["digital"], "print": counts["all"] - counts["digital"]}
            for key, value in values.items():
                per_year[key].append(value)
                totals[key] += value          # a running total only ever goes up
                running[key].append(totals[key])

        world = self.world_estimate()
        def extreme(key, pick):
            value = pick(per_year[key])
            return {"year": years[per_year[key].index(value)], "count": value}

        return {"years": years, "per_year": per_year, "running": running, "totals": totals, "world": world,
                "problem": self._get("openlibrary_problem"),
                "before": before, "first_year": years[0], "last_year": years[-1],
                "peaks": {"print": extreme("print", max), "digital": extreme("digital", max)},
                "lows": {"print": extreme("print", min), "digital": extreme("digital", min)}}


def _cloudflare(url, params, headers):
    response = sources._get(url, params=params, headers=headers, timeout=20)
    if response.status_code in (401, 403):
        raise sources.SourceError("Cloudflare didn't accept the API token. Check CLOUDFLARE_API_TOKEN in Vercel.")
    if response.status_code != 200:
        raise sources.SourceError(f"Cloudflare answered with error {response.status_code}.")
    data = response.json()
    if not data.get("success", True):
        raise sources.SourceError("Cloudflare said: " + "; ".join(e.get("message", "") for e in data.get("errors", [])))
    return data["result"]


def _pick(values, which):
    """Cloudflare names the classes 'human'/'bot' (or LIKELY_HUMAN / LIKELY_AUTOMATED)."""
    names = {"human": ("human", "LIKELY_HUMAN"), "bot": ("bot", "LIKELY_AUTOMATED")}[which]
    for name in names:
        if name in values:
            return values[name]
    raise KeyError(which)


def _human_bot(summary):
    return float(_pick(summary, "human")), float(_pick(summary, "bot"))


def _open_library_year(year):
    """{"all": books first published that year, "digital": how many have a digital copy},
    or a short description of what went wrong (the year is tried again later)."""
    span = "[* TO 1899]" if year == "before" else str(year)

    def count(query):
        response = sources._get(f"{sources.OPEN_LIBRARY}/search.json",
                                params={"q": query, "limit": 1, "fields": "key"}, timeout=40)
        if response.status_code in (429, 503):
            raise sources.SourceError("Open Library is busy (too many requests), so Tripwire is slowing down.")
        if response.status_code != 200:
            raise sources.SourceError(f"Open Library answered with error {response.status_code}.")
        data = response.json()
        return int(data.get("numFound", data.get("num_found", 0)))

    try:
        return {"all": count(f"first_publish_year:{span}"),
                "digital": count(f"first_publish_year:{span} AND has_fulltext:true")}
    except sources.SourceError as exc:
        return str(exc)
    except (ValueError, TypeError, AttributeError):
        return "Open Library's answer wasn't in the expected format."

"""Tests for Tripwire. Run with:   python -m unittest -v

These never touch the internet: Wikipedia and web pages are replaced by small
pretend versions whose content the tests control."""

import os
import tempfile
import unittest
from unittest import mock

from tripwire import changes, sources
from tripwire.checker import Tripwire


class CompareTests(unittest.TestCase):
    def test_identical_text_has_no_changes(self):
        result = changes.compare("The Warli live in Maharashtra.", "The Warli live in Maharashtra.")
        self.assertEqual(result["hunks"], [])
        self.assertEqual(result["category"], "formatting")

    def test_punctuation_only(self):
        result = changes.compare("The Warli live in Maharashtra.", "The Warli live in Maharashtra!")
        self.assertEqual(result["category"], "formatting")

    def test_name_respelling_is_tiny_but_flagged(self):
        result = changes.compare("The Kunbi people speak Konkani.", "The Kunbi people speak Konkanni.")
        self.assertEqual(result["category"], "tiny")
        self.assertEqual(result["name_changes"], [("Konkani", "Konkanni")])
        self.assertIn("possible name spelling change: Konkani → Konkanni", changes.describe(result))

    def test_repeated_respelling_listed_once(self):
        result = changes.compare("Warli art. The Warli live here.", "Warlee art. The Warlee live here.")
        self.assertEqual(result["name_changes"], [("Warli", "Warlee")])

    def test_lowercase_typo_is_not_a_name(self):
        result = changes.compare("They farm teh land.", "They farm the land.")
        self.assertEqual(result["spelling_changes"], [("teh", "the")])
        self.assertEqual(result["name_changes"], [])

    def test_ordinary_edit(self):
        before = "The Gawda are a community in Goa. " * 3
        after = before + "They are known for their harvest festivals and songs sung in the fields at night."
        result = changes.compare(before, after)
        self.assertEqual(result["category"], "edit")
        self.assertEqual(result["words_removed"], 0)

    def test_large_removal(self):
        history = " ".join(f"word{i}" for i in range(300))
        result = changes.compare("Intro. " + history + " End.", "Intro. End.")
        self.assertEqual(result["category"], "removal")
        self.assertEqual(result["words_removed"], 300)

    def test_major_change(self):
        old = " ".join(f"old{i}" for i in range(80))
        new = " ".join(f"new{i}" for i in range(80))
        self.assertEqual(changes.compare(old, new)["category"], "major")


class SourceHelperTests(unittest.TestCase):
    def test_html_to_text_drops_clutter(self):
        html = """<html><head><title>x</title><style>p{}</style></head><body>
            <p>The Siddi<sup class="reference">[1]</sup> community.<br>Second line</p>
            <script>alert(1)</script><span class="mw-editsection">[edit]</span>
            <img src="a.png"><p>Fish &amp; rice</p></body></html>"""
        self.assertEqual(sources.html_to_text(html), "The Siddi community.\nSecond line\nFish & rice")

    def test_parse_wikipedia_target(self):
        self.assertEqual(sources.parse_wikipedia_target("Adivasi"), ("en", "Adivasi"))
        self.assertEqual(sources.parse_wikipedia_target("https://en.wikipedia.org/wiki/Warli_painting"),
                         ("en", "Warli painting"))
        self.assertEqual(sources.parse_wikipedia_target("gom.m.wikipedia.org/wiki/%E0%A4%97%E0%A5%8B%E0%A4%82%E0%A4%AF"),
                         ("gom", "गोंय"))
        with self.assertRaises(sources.SourceError):
            sources.parse_wikipedia_target("https://example.com/page")

    def test_is_wikipedia_link(self):
        self.assertTrue(sources.is_wikipedia_link("https://en.wikipedia.org/wiki/Adivasi"))
        self.assertFalse(sources.is_wikipedia_link("https://example.com/wiki/Adivasi"))


class FakeWikipedia:
    """A pretend Wikipedia holding one list of versions per article."""
    articles = {}

    def __init__(self, lang="en"):
        self.lang = lang

    def revision_at(self, title, days_ago=0):
        revs = self.articles[title]
        rev = revs[0] if days_ago else revs[-1]
        return {"title": title, "revid": rev["revid"], "timestamp": "2026-10-01T00:00:00Z"}

    def revisions_since(self, title, revid):
        if title not in self.articles:
            return None
        return [dict(r, timestamp="2026-10-28T00:00:00Z") for r in self.articles[title] if r["revid"] > revid]

    def text_of(self, revid):
        for revs in self.articles.values():
            for r in revs:
                if r["revid"] == revid:
                    return r["text"]
        raise sources.SourceError("no such version")

    revision_url = staticmethod(lambda revid: f"https://en.wikipedia.org/w/index.php?oldid={revid}")
    diff_url = staticmethod(lambda old, new: f"https://en.wikipedia.org/w/index.php?diff={new}&oldid={old}")


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Set TRIPWIRE_TEST_DB to a Postgres address to run the same tests on Postgres.
        self.db_path = os.environ.get("TRIPWIRE_TEST_DB") or os.path.join(self.tmp.name, "test.db")
        if self.db_path.startswith("postgres"):
            import psycopg
            with psycopg.connect(self.db_path, autocommit=True) as conn:
                conn.execute("DROP TABLE IF EXISTS entries, watches, themes, users CASCADE")
        self.tw = Tripwire(self.db_path, archive=False)
        self.user = self.tw.create_user("purple@example.org", "correct horse")
        FakeWikipedia.articles = {"Warli": [
            {"revid": 1, "user": "A", "comment": "start", "text": "The Warli are an Adivasi people of Maharashtra."},
        ]}
        patcher = mock.patch.object(sources, "Wikipedia", FakeWikipedia)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.theme = self.tw.add_theme(self.user, "Indigenous communities")

    def tearDown(self):
        self.tw.conn.close()
        self.tmp.cleanup()

    def add_version(self, revid, text, user="B", comment="copyedit"):
        FakeWikipedia.articles["Warli"].append({"revid": revid, "user": user, "comment": comment, "text": text})

    def entries(self):
        return self.tw.conn.execute("SELECT * FROM entries ORDER BY id").fetchall()

    def test_theme_limit(self):
        self.tw.add_theme(self.user, "Languages")
        self.tw.add_theme(self.user, "Oral history")
        with self.assertRaises(ValueError):
            self.tw.add_theme(self.user, "One too many")
        # The limit is per person: someone else can still make their own.
        other = self.tw.create_user("friend@example.org", "another password")
        self.tw.add_theme(other, "Languages")

    def test_no_change_means_no_entry(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.assertIsNone(self.tw.check_watch(watch))
        self.assertEqual(self.entries(), [])

    def test_change_is_saved_with_before_and_after(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warli are an Adivasi people of Maharashtra and Gujarat.", user="B")
        self.add_version(3, "The Warlee are an Adivasi people of Maharashtra and Gujarat.", user="C", comment="spelling")

        entry_id = self.tw.check_watch(watch)
        [entry] = self.entries()
        self.assertEqual(entry["id"], entry_id)
        self.assertIn("Maharashtra.", entry["before_text"])
        self.assertIn("Warlee", entry["after_text"])
        self.assertIn("Warli → Warlee", entry["summary"])
        self.assertIn("diff=3&oldid=1", entry["compare_url"])
        details = self.tw.conn.execute("SELECT details FROM entries").fetchone()["details"]
        self.assertIn('"editors": ["B", "C"]', details)

        # The baseline moved forward, so checking again finds nothing new.
        self.assertIsNone(self.tw.check_watch(watch))
        self.assertEqual(len(self.entries()), 1)

    def test_look_back_starts_from_an_older_version(self):
        self.add_version(2, "The Warli are an Adivasi people of Maharashtra, India.")
        watch = self.tw.add_watch(self.theme, "Warli", days_ago=30)
        self.assertIsNotNone(self.tw.check_watch(watch))

    def test_keep_and_disregard(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warli are an Adivasi people of India.")
        entry_id = self.tw.check_watch(watch)
        self.tw.keep(entry_id)
        self.assertEqual(self.entries()[0]["kept"], 1)
        self.tw.disregard(entry_id)
        self.assertEqual(self.entries(), [])

    def test_deleted_article_recorded_once(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        del FakeWikipedia.articles["Warli"]
        self.assertIsNotNone(self.tw.check_watch(watch))
        self.assertIsNone(self.tw.check_watch(watch))
        [entry] = self.entries()
        self.assertEqual(entry["category"], "page_removed")
        self.assertIn("Adivasi", entry["before_text"])

    def test_web_page_change_and_removal(self):
        pages = {"https://example.org/history": {"status": "ok", "text": "Founded by the village elders in 1902."}}
        with mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            watch = self.tw.add_watch(self.theme, "https://example.org/history")
            self.assertIsNone(self.tw.check_watch(watch))
            pages["https://example.org/history"] = {"status": "ok", "text": "Founded in 1902."}
            self.assertIsNotNone(self.tw.check_watch(watch))
            pages["https://example.org/history"] = {"status": "removed", "code": 404}
            self.tw.check_watch(watch)
        categories = [e["category"] for e in self.entries()]
        self.assertEqual(categories, ["tiny", "page_removed"])

    def test_problem_fetching_is_reported_not_crashed(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        with mock.patch.object(FakeWikipedia, "revisions_since", side_effect=sources.SourceError("offline")):
            self.assertIsNone(self.tw.check_watch(watch))
        status = self.tw.conn.execute("SELECT last_status FROM watches").fetchone()["last_status"]
        self.assertEqual(status, "Couldn't check: offline")

    def test_weekly_summary(self):
        self.assertIn("no changes across your 0 tracked items", self.tw.weekly_summary(self.user)["text"])
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        self.tw.check_watch(watch)
        text = self.tw.weekly_summary(self.user)["text"]
        self.assertIn("1 change across 1 of your 1 tracked items", text)
        self.assertIn("Names that changed spelling: Warli → Warlee (Warli)", text)

    def test_accounts(self):
        self.assertEqual(self.tw.log_in(" Purple@Example.org ", "correct horse")["id"], self.user)
        self.assertIsNone(self.tw.log_in("purple@example.org", "wrong password"))
        self.assertIsNone(self.tw.log_in("nobody@example.org", "correct horse"))
        with self.assertRaises(ValueError):
            self.tw.create_user("purple@example.org", "some password")  # already taken
        with self.assertRaises(ValueError):
            self.tw.create_user("new@example.org", "short")
        with self.assertRaises(ValueError):
            self.tw.create_user("not an email", "long enough password")
        stored = self.tw.conn.execute("SELECT password_hash FROM users").fetchone()["password_hash"]
        self.assertNotIn("correct horse", stored)  # passwords are never stored as typed

    def test_archive_copies_saved_for_before_and_after(self):
        self.tw.archive = True
        watch = self.tw.add_watch(self.theme, "Warli")
        with mock.patch.object(sources, "archive_save", lambda url, timeout: "https://web.archive.org/web/1/" + url):
            self.assertEqual(self.tw.save_archive_copies(), 1)
            self.add_version(2, "The Warli are an Adivasi people of India.")
            self.tw.check_watch(watch)
            self.assertEqual(self.tw.save_archive_copies(), 1)
            self.assertEqual(self.tw.save_archive_copies(), 0)  # nothing left to save
        [entry] = self.entries()
        self.assertTrue(entry["before_archive_url"].endswith("oldid=1"))
        self.assertTrue(entry["after_archive_url"].endswith("oldid=2"))

    def test_archive_failure_is_retried_later(self):
        self.tw.archive = True
        self.tw.add_watch(self.theme, "Warli")
        with mock.patch.object(sources, "archive_save", side_effect=sources.SourceError("busy")):
            self.assertEqual(self.tw.save_archive_copies(), 0)
        with mock.patch.object(sources, "archive_save", lambda url, timeout: "https://web.archive.org/web/1/x"):
            self.assertEqual(self.tw.save_archive_copies(), 1)


class WebAppTests(CheckerTests):
    """Click through the web app with the pretend Wikipedia."""

    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, ARCHIVE=False, TESTING=True)
        self.client = webapp.app.test_client()

    def log_in(self, email="purple@example.org", password="correct horse"):
        return self.client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    def test_pages_need_an_account(self):
        for path in ["/", "/archive", "/summary", "/entries/1"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["Location"].endswith("/login"))

    def test_signup_and_login(self):
        response = self.client.post("/signup", data={"email": "new@example.org", "password": "long enough"},
                                    follow_redirects=True)
        self.assertIn(b"new@example.org", response.data)
        self.assertIn(b"Start with a theme", response.data)
        self.client.post("/logout")
        self.assertIn(b"don&#39;t match", self.log_in(password="wrong").data)
        self.assertIn(b"Indigenous communities", self.log_in().data)

    def test_pages_load(self):
        self.log_in()
        response = self.client.post(f"/themes/{self.theme}/watches", data={"target": "Warli", "days_ago": "0"},
                                    follow_redirects=True)
        self.assertIn(b"Now watching it", response.data)
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        response = self.client.post("/check", follow_redirects=True)
        self.assertIn(b"Name spelling changed", response.data)
        self.assertIn(b"Warlee", self.client.get("/entries/1").data)
        self.assertIn(b"1 change across 1", self.client.get("/summary").data)
        self.assertEqual(self.client.get("/").status_code, 200)
        with self.client.get("/static/style.css") as css:
            self.assertEqual(css.status_code, 200)

        # Disregard redirects back inside the site, never to another website.
        response = self.client.post("/entries/1/disregard", data={"next": "//evil.example"})
        self.assertTrue(response.headers["Location"].endswith("/archive"))
        self.assertEqual(self.entries(), [])

    def test_people_cannot_touch_each_others_things(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        entry = self.tw.check_watch(watch)
        self.tw.create_user("stranger@example.org", "stranger danger")
        self.log_in("stranger@example.org", "stranger danger")

        self.assertNotIn(b"Warli", self.client.get("/").data)
        self.assertNotIn(b"Warli", self.client.get("/archive").data)
        self.assertNotIn(b"Warlee", self.client.get(f"/entries/{entry}", follow_redirects=True).data)
        for path in [f"/entries/{entry}/disregard", f"/entries/{entry}/keep", f"/watches/{watch}/delete",
                     f"/watches/{watch}/check", f"/themes/{self.theme}/delete", f"/themes/{self.theme}/watches"]:
            self.assertEqual(self.client.post(path, data={"target": "X"}).status_code, 404, path)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM watches").fetchone()["n"], 1)

    def test_daily_check_needs_the_secret(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warli are an Adivasi people of India.")
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self.assertEqual(self.client.get("/cron").status_code, 403)
            self.assertEqual(self.client.get("/cron", headers={"Authorization": "Bearer wrong"}).status_code, 403)
            response = self.client.get("/cron", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(response.json["new_entries"], 1)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CRON_SECRET", None)
            self.assertEqual(self.client.get("/cron").status_code, 403)  # no secret set: always refused


class SetupTests(unittest.TestCase):
    """What happens when Tripwire's online settings are missing or named differently."""

    def test_database_found_under_other_names(self):
        from tripwire import db
        cases = [
            ({"DATABASE_URL": "postgresql://a"}, "DATABASE_URL"),
            ({"POSTGRES_URL": "postgres://b"}, "POSTGRES_URL"),
            ({"STORAGE_DATABASE_URL": "postgresql://c"}, "STORAGE_DATABASE_URL"),
            ({"TRIPWIRE_POSTGRES_URL": "postgresql://d"}, "TRIPWIRE_POSTGRES_URL"),
            ({"POSTGRES_PRISMA_URL": "prisma://x"}, None),  # not a plain Postgres address
            ({}, None),
        ]
        for env, expected in cases:
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(db.find_database_url()[0], expected, env)

    def test_plain_words_error_page(self):
        import app as webapp
        webapp.app.config.update(DB_URL=None, ARCHIVE=False, TESTING=False, PROPAGATE_EXCEPTIONS=False)
        client = webapp.app.test_client()
        with mock.patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            response = client.post("/login", data={"email": "a@b.co", "password": "whatever1"})
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"can&#39;t find its database", response.data)
        self.assertIn(b"Storage tab", response.data)

        with mock.patch.dict(os.environ, {"VERCEL": "1", "DATABASE_URL": "postgresql://nobody@127.0.0.1:1/x"},
                             clear=True):
            response = client.post("/login", data={"email": "a@b.co", "password": "whatever1"})
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"couldn&#39;t connect", response.data)
        self.assertNotIn(b"nobody@127.0.0.1", response.data)  # never show the address itself
        webapp.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=None)

    def test_sign_up_is_easy_to_find(self):
        import app as webapp
        response = webapp.app.test_client().get("/login")
        self.assertIn(b"Make an account", response.data)


if __name__ == "__main__":
    unittest.main()

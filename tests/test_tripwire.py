"""Tests for Tripwire. Run with:   python -m unittest -v

These never touch the internet: Wikipedia and web pages are replaced by small
pretend versions whose content the tests control."""

import os
import re
import time
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

    article_url = staticmethod(lambda title: "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"))
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
                conn.execute("DROP TABLE IF EXISTS site_keys, shelf_items, before_checks, feedback, stats_cache, book_jobs, book_comparisons, archive_copies, entries, watches, themes, users CASCADE")
        self.tw = Tripwire(self.db_path)
        self.user = self.tw.create_user("purple@example.org", "correct horse")
        FakeWikipedia.articles = {"Warli": [
            {"revid": 1, "user": "A", "comment": "start", "text": "The Warli are an Adivasi people of Maharashtra."},
        ]}
        patcher = mock.patch.object(sources, "Wikipedia", FakeWikipedia)
        patcher.start()
        self.addCleanup(patcher.stop)
        from tripwire import stats
        no_books = mock.patch.object(stats, "_open_library_year", lambda year: None)  # never reach the real Open Library
        no_books.start()
        self.addCleanup(no_books.stop)
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

    def make_major_change(self, revid=2):
        self.add_version(revid, " ".join(f"rewritten{i}" for i in range(150)))

    def test_single_links_dont_count_as_themes(self):
        single = self.tw.single_links(self.user)
        self.assertEqual(self.tw.single_links(self.user), single)  # made once, then reused
        self.tw.add_theme(self.user, "Languages")
        self.tw.add_theme(self.user, "Formula One")  # 3 real themes, plus Single links
        self.tw.add_watch(single, "Warli")
        self.assertEqual(self.tw.weekly_summary(self.user)["tracked"], 1)

    def test_which_changes_are_offered_a_public_copy(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")  # name respelling only
        tiny = self.tw.own_entry(self.user, self.tw.check_watch(watch))
        self.assertEqual(self.tw.copy_options(entry=tiny), [])
        self.make_major_change(3)
        major = self.tw.own_entry(self.user, self.tw.check_watch(watch))
        options = self.tw.copy_options(entry=major)
        self.assertEqual([o["url"][-7:] for o in options], ["oldid=2", "oldid=3"])  # before and after
        watched = self.tw.own_watch(self.user, watch)
        self.assertEqual(len(self.tw.copy_options(watch=watched)), 1)

    def test_every_visit_noise_is_not_a_change(self):
        """Bits like "updated 5 minutes ago" change on every visit; the daily check ignores them."""
        pages = {"https://news.example.org/lesotho": {"status": "ok", "text": "Lesotho news\nUpdated 5 minutes ago\nThe dam opened."}}
        with mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            watch = self.tw.add_watch(self.theme, "https://news.example.org/lesotho")
            pages["https://news.example.org/lesotho"]["text"] = "Lesotho news\nUpdated 2 hours ago\n1,204 people watching now\nThe dam opened."
            self.assertIsNone(self.tw.check_watch(watch))
            pages["https://news.example.org/lesotho"]["text"] = "Lesotho news\nUpdated just now\nThe dam closed."
            entry = self.tw.check_watch(watch)
        self.assertIsNotNone(entry)  # a real change still counts
        details = self.tw.conn.execute("SELECT details FROM entries WHERE id = ?", (entry,)).fetchone()["details"]
        self.assertNotIn("ago", details)
        self.assertIn("closed", details)

    def test_long_pages_are_compared_whole(self):
        long_text = " ".join(f"Chapter {i} tells of the river and the old stone house." for i in range(4000)) + " The end."
        pages = {"https://books.example.org/b.txt": {"status": "ok", "text": long_text}}
        with mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            watch = self.tw.add_watch(self.theme, "https://books.example.org/b.txt")
            pages["https://books.example.org/b.txt"]["text"] = long_text.replace("The end.", "The new end.")
            self.assertIsNotNone(self.tw.check_watch(watch))  # a change after the first 200,000 characters

    def test_removed_page_cannot_get_a_new_copy(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        del FakeWikipedia.articles["Warli"]
        removed = self.tw.own_entry(self.user, self.tw.check_watch(watch))
        self.assertEqual(self.tw.copy_options(entry=removed), [])
        self.assertEqual(self.tw.copy_options(watch=self.tw.own_watch(self.user, watch)), [])

    def test_nothing_is_ever_saved_automatically(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.make_major_change()
        with mock.patch.object(sources, "archive_save", side_effect=AssertionError("saved without asking!")):
            self.tw.check_all()
        self.assertEqual(self.tw.copies(self.user), [])
        self.assertEqual(len(self.tw.weekly_summary(self.user)["offer_copy"]), 1)

    def test_archive_save_without_a_link_is_not_guessed(self):
        response = mock.Mock(status_code=200, headers={}, url="https://web.archive.org/save/x")
        with mock.patch.object(sources, "_get", return_value=response):
            self.assertIsNone(sources.archive_save("https://example.org"))


class WebAppTests(CheckerTests):
    """Click through the web app with the pretend Wikipedia."""

    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()

    def log_in(self, email="purple@example.org", password="correct horse"):
        return self.client.post("/login", data={"email": email, "password": password}, follow_redirects=True)

    def test_pages_open_without_an_account(self):
        for path in ["/watching", "/changes", "/books", "/shelf", "/stats", "/about"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
        # Old addresses lead to where those things are now.
        for old, new in [("/archive", "/shelf#public-copies"), ("/summary", "/changes#summary"),
                         ("/videos", "/books#videos"), ("/live", "/watching?live=1#live"), ("/before", "/watching")]:
            self.assertTrue(self.client.get(old).headers["Location"].endswith(new), old)
        self.assertEqual(self.client.get("/entries/1").status_code, 302)  # someone else's: not shown
        self.assertEqual(self.client.get("/books/1").status_code, 404)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"], 1)  # looking makes no guest
        # Public copies and emails need a real account.
        response = self.client.get("/copy/entry/1")
        self.assertTrue(response.headers["Location"].endswith("/signup"))
        self.assertTrue(self.client.post("/summary/email", data={"every": "daily"}).headers["Location"].endswith("/signup"))

    def test_guest_adds_things_then_keeps_them_with_an_account(self):
        self.client.post("/themes", data={"name": "Indigenous communities"})
        guest = self.tw.conn.execute("SELECT * FROM users WHERE guest = 1").fetchone()
        theme = self.tw.conn.execute("SELECT id FROM themes WHERE user_id = ?", (guest["id"],)).fetchone()["id"]
        self.client.post(f"/themes/{theme}/watches", data={"target": "Warli"})
        page = self.client.get("/watching").data.decode()
        self.assertIn("Warli", page)
        self.assertIn("trying Tripwire without an account", page)
        self.assertIn("Keep what you", self.client.get("/signup").data.decode())
        self.assertEqual(self.tw.log_in(guest["email"], "!"), None)  # nobody can log in as a guest
        # The guest's things aren't in the daily run, and aren't counted as people.
        self.assertEqual(self.tw.totals()["people"], 1)
        self.assertEqual(self.tw.check_all(), [])
        self.assertIsNone(self.tw.conn.execute("SELECT last_checked_at FROM watches").fetchone()["last_checked_at"])
        # Signing up keeps everything.
        self.pass_human_check()
        response = self.client.post("/signup", data={"email": "keeper@example.org", "password": "long enough"},
                                    follow_redirects=True)
        self.assertIn(b"HUMAN AFTER ALL", response.data)
        user = self.tw.log_in("keeper@example.org", "long enough")
        self.assertEqual(user["id"], guest["id"])
        self.assertEqual(Tripwire.summary_every(user), "weekly")
        page = self.client.get("/watching").data.decode()
        self.assertIn("Warli", page)
        self.assertNotIn("trying Tripwire without an account", page)

    def test_guest_things_move_into_an_account_on_log_in(self):
        self.client.post("/single-links", data={"target": "Warli"})
        self.client.post("/themes", data={"name": "Language & memory"})
        self.client.post("/shelf/file", data={"title": "Gazette 12", "file_name": "gazette.pdf",
                                              "read_text": "Notice of the gazette."})
        self.log_in()
        page = self.client.get("/watching").data.decode()
        self.assertIn("Warli", page)
        self.assertIn("Language &amp; memory", page)
        self.assertIn("Gazette 12", self.client.get("/shelf").data.decode())
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM users WHERE guest = 1").fetchone()["n"], 0)

    def test_redirects_stay_on_this_site(self):
        self.log_in()
        watch = self.tw.add_watch(self.theme, "Warli")
        for sneaky in ["//evil.example", "/\\evil.example", "https://evil.example", "/ok\r\nSet-Cookie: x=1"]:
            response = self.client.post(f"/watches/{watch}/check", data={"next": sneaky})
            self.assertNotIn("evil", response.headers["Location"])
            self.assertNotIn("Set-Cookie", response.headers["Location"])
        self.assertTrue(self.client.get("/language/en?next=//evil.example").headers["Location"].endswith("/"))

    def test_browsers_are_asked_to_be_careful(self):
        headers = self.client.get("/").headers
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "same-origin")
        self.assertIn("camera=()", headers["Permissions-Policy"])

    def test_too_many_guests_at_once_must_make_an_account(self):
        import app as webapp
        with mock.patch.object(webapp, "GUESTS_PER_HOUR", 2):
            for _ in range(2):
                visitor = webapp.app.test_client()
                visitor.post("/themes", data={"name": "A theme"})
            response = self.client.post("/themes", data={"name": "One too many"})
        self.assertTrue(response.headers["Location"].endswith("/signup"))
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM users WHERE guest = 1").fetchone()["n"], 2)

    def test_delete_my_account(self):
        self.log_in()
        self.tw.add_watch(self.theme, "Warli")
        self.client.post("/feedback", data={"message": "Lovely"})
        page = self.client.get("/account").data.decode()
        self.assertIn("1 watched item", page)
        self.assertIn("Delete my account", page)
        # A wrong password deletes nothing.
        self.client.post("/account/delete", data={"password": "wrong", "sure": "yes"})
        self.assertIsNotNone(self.tw.user(self.user))
        self.client.post("/account/delete", data={"password": "correct horse"})  # not ticked: nothing
        self.assertIsNotNone(self.tw.user(self.user))
        page = self.client.post("/account/delete", data={"password": "correct horse", "sure": "yes"},
                                follow_redirects=True).data.decode()
        self.assertIn("have been deleted", page)
        self.assertIsNone(self.tw.user(self.user))
        for table in ("themes", "watches", "entries"):
            self.assertEqual(self.tw.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"], 0, table)
        self.assertEqual(self.tw.totals()["feedback"], 1)  # stays, with no name attached
        self.assertTrue(self.client.get("/account").headers["Location"].endswith("/signup"))

    def test_guest_shelf_is_smaller(self):
        from tripwire import shelf
        with mock.patch.object(shelf, "GUEST_SHELF_BYTES", 10):
            page = self.client.post("/shelf/file", data={"title": "Big", "file_name": "big.txt",
                                                         "read_text": "far more than ten bytes of words"},
                                    follow_redirects=True).data.decode()
        self.assertIn("Without an account, a shelf holds up to", page)

    def test_private_network_links_are_refused(self):
        self.log_in()
        for link in ["http://127.0.0.1:5000/admin", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"]:
            with self.assertRaises(sources.SourceError):
                sources._check_public(link)
        page = self.client.post("/live/check", json={"url": "http://127.0.0.1:5000/"})
        self.assertEqual(page.status_code, 502)
        self.assertIn("private network", page.json["error"])

    def test_too_many_wrong_passwords_pause_logging_in(self):
        for _ in range(5):
            self.assertIn(b"don&#39;t match", self.log_in(password="wrong guess").data)
        page = self.log_in().data.decode()  # even the right password waits
        self.assertIn("paused logging in", page)
        self.tw.conn.execute("UPDATE users SET failed_login_at = '2020-01-01T00:00:00Z'")  # 15 minutes later
        self.assertIn("WELCOME BACK", self.log_in().data.decode())
        self.assertEqual(self.tw.user(self.user)["failed_logins"], 0)

    def test_forms_from_other_websites_are_refused(self):
        self.log_in()
        self.assertEqual(self.client.post("/themes", data={"name": "X"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.post("/logout", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/logout", headers={"Origin": "null"}).status_code, 403)
        self.assertIn("WELCOME BACK", self.client.get("/").data.decode())  # still logged in
        ok = self.client.post("/themes", data={"name": "Language"}, headers={"Sec-Fetch-Site": "same-origin"})
        self.assertEqual(ok.status_code, 302)
        ok = self.client.post("/themes", data={"name": "Memory"}, headers={"Origin": "http://localhost"})
        self.assertEqual(ok.status_code, 302)

    def test_old_guests_are_tidied_away(self):
        self.client.post("/single-links", data={"target": "Warli"})
        self.tw.conn.execute("UPDATE users SET created_at = '2020-01-01T00:00:00Z' WHERE guest = 1")
        self.tw.delete_old_guests()
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM watches").fetchone()["n"], 0)
        self.assertNotIn("Warli", self.client.get("/watching").data.decode())  # the visit simply starts again

    def test_opening_plays_once_per_visit(self):
        self.assertIn('id="intro"', self.client.get("/").data.decode())
        self.assertNotIn('id="intro"', self.client.get("/").data.decode())
        self.assertIn('id="intro"', self.client.get("/?intro").data.decode())  # replay on purpose
        self.log_in()
        self.assertNotIn('id="intro"', self.client.get("/").data.decode())  # not again after logging in

    def pass_human_check(self):
        """Do what the sign-up page does: answer, solve the background proof of work, send."""
        from tripwire import humancheck
        import app as webapp
        webapp.app.config.update(HUMAN_CHECK_BITS=4, HUMAN_CHECK_MIN_SECONDS=0)
        with mock.patch.object(humancheck, "_question", lambda: ("What is two plus two?", 4)):
            self.assertIn(b"What is two plus two?", self.client.get("/signup").data)
        with self.client.session_transaction() as sess:
            salt = sess["human_check"]["salt"]
        nonce = next(n for n in range(100000) if humancheck.pow_ok(salt, n, 4))
        return self.client.post("/human-check", json={"answer": "four", "nonce": nonce, "website": ""}).json

    def test_signup_and_login(self):
        response = self.client.post("/signup", data={"email": "new@example.org", "password": "long enough"},
                                    follow_redirects=True)
        self.assertIn(b"Please finish the human check first", response.data)  # no account without the check
        self.assertEqual(self.pass_human_check(), {"passed": True, "message": "DONE."})
        response = self.client.post("/signup", data={"email": "new@example.org", "password": "long enough"},
                                    follow_redirects=True)
        self.assertIn(b"HUMAN AFTER ALL", response.data)  # shown once, straight after signing up
        self.assertTrue(self.client.get("/welcome").headers["Location"].endswith("/"))  # not again
        response = self.client.get("/")
        self.assertIn(b"WELCOME BACK", response.data)  # then the computer
        handle = self.tw.log_in("new@example.org", "long enough")["handle"]
        self.assertTrue(handle)
        self.assertIn(handle.encode(), response.data)          # known by a random username...
        self.assertNotIn(b"new@example.org", response.data)    # ...not by their email
        self.assertIn(b"Start with a theme", self.client.get("/watching").data)
        self.client.post("/logout")
        self.assertIn(b"don&#39;t match", self.log_in(password="wrong").data)
        self.log_in()
        self.assertIn(b"Indigenous communities", self.client.get("/watching").data)

    def test_human_check_rejects_bots(self):
        from tripwire import humancheck
        import app as webapp
        webapp.app.config.update(HUMAN_CHECK_BITS=4, HUMAN_CHECK_MIN_SECONDS=0)
        with mock.patch.object(humancheck, "_question", lambda: ("What is two plus two?", 4)):
            self.client.get("/signup")
        with self.client.session_transaction() as sess:
            salt = sess["human_check"]["salt"]
        good = next(n for n in range(100000) if humancheck.pow_ok(salt, n, 4))
        bad = next(n for n in range(100000) if not humancheck.pow_ok(salt, n, 4))
        # Filled in the hidden trap field: refused, and the question changes.
        result = self.client.post("/human-check", json={"answer": "4", "nonce": good, "website": "http://spam"}).json
        self.assertFalse(result["passed"])
        self.assertIn("question", result)
        with self.client.session_transaction() as sess:
            self.assertNotEqual(sess["human_check"]["salt"], salt)  # a new challenge, so guessing doesn't help
            salt = sess["human_check"]["salt"]
        bad = next(n for n in range(100000) if not humancheck.pow_ok(salt, n, 4))  # wrong for *this* challenge
        self.assertFalse(self.client.post("/human-check", json={"answer": "4", "nonce": bad}).json["passed"])

    def test_human_check_rules(self):
        from tripwire import humancheck
        with mock.patch.object(humancheck, "_question", lambda: ("What is seven plus five?", 12)):
            c = humancheck.new_challenge("secret", bits=4)
        nonce = next(n for n in range(100000) if humancheck.pow_ok(c["salt"], n, 4))
        later = c["issued"] + 10
        self.assertTrue(humancheck.check(c, "secret", "12", nonce, now=later)[0])
        self.assertTrue(humancheck.check(c, "secret", "Twelve.", nonce, now=later)[0])
        self.assertFalse(humancheck.check(c, "secret", "13", nonce, now=later)[0])
        self.assertIn("quicker than a human", humancheck.check(c, "secret", "12", nonce, now=c["issued"] + 1)[1])
        self.assertIn("expired", humancheck.check(c, "secret", "12", nonce, now=c["issued"] + 3600)[1])
        self.assertFalse(humancheck.check(c, "other secret", "12", nonce, now=later)[0])
        # The answer itself is never stored where a visitor could read it: only a signature of it.
        self.assertNotIn(12, c.values())
        self.assertNotIn("12", [c["answer"], c["question"]])
        self.assertEqual(len(c["answer"]), 64)

    def test_books_intro_screen(self):
        self.log_in()
        page = self.client.get("/books").data.decode()
        self.assertIn("YOU ARE NOW ENTERING UNRESTRICTED KNOWLEDGE PRESERVATION", page)
        page = self.client.get("/books", headers={"Referer": "http://localhost/books/1"}).data.decode()
        self.assertNotIn("UNRESTRICTED KNOWLEDGE", page)  # not when moving around inside Books

    def test_rabbit_on_every_page(self):
        for path in ["/", "/login", "/signup", "/no-such-page"]:
            self.assertIn(b"rabbit.png", self.client.get(path).data, path)
        self.log_in()
        for path in ["/watching", "/books", "/stats"]:
            self.assertIn(b"rabbit.png", self.client.get(path).data, path)

    def test_home_computer(self):
        page = self.client.get("/").data.decode()  # logged out: everything, plus logging in
        self.assertIn("[L] LOG IN", page)
        self.assertIn("[M] MAKE AN ACCOUNT", page)
        self.assertIn("[1] WATCHING", page)
        self.log_in()
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        self.tw.check_watch(watch)
        page = self.client.get("/").data.decode()
        self.assertIn("WELCOME BACK", page)
        self.assertIn("[1] WATCHING", page)
        self.assertIn("1 item<", page)
        self.assertIn("1 waiting", page)
        self.assertIn('href="/watching"', page)

    def test_error_pages_keep_the_site_style(self):
        response = self.client.get("/no-such-page")
        self.assertEqual(response.status_code, 404)
        page = response.data.decode()
        self.assertIn("ERROR 404: FILE NOT FOUND", page)
        self.assertIn('class="brand"', page)  # same header as the rest of the site
        self.assertIn("/static/style.css", page)
        response = self.client.get("/logout")  # only accepts POST
        self.assertEqual(response.status_code, 405)
        self.assertIn("WRONG KIND OF REQUEST", response.data.decode())

    def test_pages_load(self):
        self.log_in()
        response = self.client.post(f"/themes/{self.theme}/watches", data={"target": "Warli", "days_ago": "0"},
                                    follow_redirects=True)
        self.assertIn(b"Now watching it", response.data)
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        response = self.client.post("/check", follow_redirects=True)
        self.assertIn(b"Name spelling changed", response.data)
        self.assertIn(b"Warlee", self.client.get("/entries/1").data)
        self.assertIn(b"1 change across 1", self.client.get("/changes").data)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/watching").status_code, 200)
        with self.client.get("/static/style.css") as css:
            self.assertEqual(css.status_code, 200)

        # Disregard redirects back inside the site, never to another website.
        response = self.client.post("/entries/1/disregard", data={"next": "//evil.example"})
        self.assertTrue(response.headers["Location"].endswith("/changes"))
        self.assertEqual(self.entries(), [])

    def test_people_cannot_touch_each_others_things(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        entry = self.tw.check_watch(watch)
        self.tw.create_user("stranger@example.org", "stranger danger")
        self.log_in("stranger@example.org", "stranger danger")

        self.assertNotIn(b"Warli", self.client.get("/watching").data)
        self.assertNotIn(b"Warli", self.client.get("/changes").data)
        self.assertNotIn(b"Warli", self.client.get("/archive").data)
        self.assertNotIn(b"Warlee", self.client.get(f"/entries/{entry}", follow_redirects=True).data)
        for path in [f"/entries/{entry}/disregard", f"/entries/{entry}/keep", f"/watches/{watch}/delete",
                     f"/watches/{watch}/check", f"/themes/{self.theme}/delete", f"/themes/{self.theme}/watches"]:
            self.assertEqual(self.client.post(path, data={"target": "X"}).status_code, 404, path)
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM watches").fetchone()["n"], 1)

    def test_public_copy_is_asked_twice(self):
        self.log_in()
        watch = self.tw.add_watch(self.theme, "Warli")
        self.make_major_change()
        entry = self.tw.check_watch(watch)
        saved_urls = []

        def fake_save(url, timeout):
            saved_urls.append(url)
            return "https://web.archive.org/web/2026/" + url

        with mock.patch.object(sources, "archive_save", fake_save):
            # Ask 1: the change page offers it, but saves nothing.
            page = self.client.get(f"/entries/{entry}").data
            self.assertIn(b"Is this important to you?", page)
            # Ask 2: the confirmation page explains, still saves nothing.
            page = self.client.get(f"/copy/entry/{entry}").data
            self.assertIn(b"public and permanent", page)
            self.assertEqual(saved_urls, [])
            # Saying yes without ticking "I understand" saves nothing.
            self.client.post(f"/copy/entry/{entry}", data={"option0": "1"})
            self.assertEqual(saved_urls, [])
            # Confirmed: only the chosen version is saved.
            self.client.post(f"/copy/entry/{entry}", data={"option1": "1", "understood": "yes"})
        self.assertEqual(len(saved_urls), 1)
        self.assertTrue(saved_urls[0].endswith("oldid=2"))
        archive_page = self.client.get("/shelf").data
        self.assertIn(b"https://web.archive.org/web/2026/", archive_page)
        self.assertIn(b"Your public copies of this change", self.client.get(f"/entries/{entry}").data)
        self.assertEqual(self.tw.weekly_summary(self.user)["offer_copy"], [])  # no longer offered

    def test_single_link_from_the_web_page(self):
        self.log_in()
        response = self.client.post("/single-links", data={"target": "Warli", "days_ago": "0"}, follow_redirects=True)
        self.assertIn(b"Now watching it", response.data)
        self.assertIn(b"Single links", response.data)
        single = self.tw.single_links(self.user)
        self.assertEqual(self.client.post(f"/themes/{single}/delete").status_code, 404)  # can't be removed

    def test_strangers_cannot_make_copies_of_your_things(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.tw.create_user("stranger@example.org", "stranger danger")
        self.log_in("stranger@example.org", "stranger danger")
        with mock.patch.object(sources, "archive_save", side_effect=AssertionError("should not save")):
            self.assertEqual(self.client.get(f"/copy/watch/{watch}").status_code, 404)
            self.assertEqual(self.client.post(f"/copy/watch/{watch}",
                                              data={"option0": "1", "understood": "yes"}).status_code, 404)

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


def make_epub(chapters):
    """A real, minimal EPUB file containing the given chapter texts."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(chapters)))
        spine = "".join(f'<itemref idref="c{i}"/>' for i in reversed(range(len(chapters))))  # reading order differs from file order
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>{items}</manifest><spine>{spine}</spine></package>')
        for i, text in enumerate(chapters):
            z.writestr(f"OEBPS/c{i}.xhtml", f"<html><body><p>{text}</p></body></html>")
    return buf.getvalue()


def make_pdf(pages):
    """A real, minimal PDF file with one line of text per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 11 Tf 40 700 Td ({text}) Tj ET".encode()
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objects)} 0 R "
                       f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        body = body if isinstance(body, bytes) else body.encode()
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


OLD_BOOK = ("Augustus Gloop was enormously fat. He killed the time by eating chocolate all day long. "
            "Mrs Twit was ugly and she killed flies with a swatter. The boy from Loompaland sang a song. ") * 3
NEW_BOOK = ("Augustus Gloop was enormous. He unalived the time by eating chocolate all day long. "
            "Mrs Twit was beastly and she unalived flies with a swatter. The boy from Lumpaland sang a song. ") * 3


class BookTests(unittest.TestCase):
    def test_read_plain_text_epub_pdf_and_html(self):
        from tripwire import books
        text, info = books.read_book("old.txt", OLD_BOOK.encode())
        self.assertEqual(info["format"], "plain text")
        text, info = books.read_book("old.epub", make_epub([OLD_BOOK[:200], OLD_BOOK[200:]]))
        self.assertEqual(info["format"], "EPUB")
        self.assertTrue(text.startswith(OLD_BOOK[200:210].strip()))  # follows the book's reading order
        text, info = books.read_book("old.pdf", make_pdf([OLD_BOOK[:90], OLD_BOOK[90:180], OLD_BOOK[180:270]]))
        self.assertEqual((info["format"], info["pages"]), ("PDF", 3))
        self.assertIn("enormously fat", text)
        text, info = books.read_book("old.html", f"<html><body><p>{OLD_BOOK}</p></body></html>".encode())
        self.assertEqual(info["format"], "web page")

    def test_text_read_from_a_scanned_pdf(self):
        from tripwire import books
        text, info = books.read_book("gazette-1998.pdf (scanned, 12 pages).txt", OLD_BOOK.encode())
        self.assertEqual((info["format"], info["pages"]), ("Scanned PDF, read on your device", 12))
        self.assertEqual(books.edition_label(books.clean_name("gazette-1998.pdf (scanned, 12 pages).txt"), info),
                         f"gazette-1998.pdf (Scanned PDF, read on your device, 12 pages, {info['words']:,} words)")

    def test_apostrophes_stay_inside_words(self):
        self.assertEqual(changes.join_tokens(changes.tokenize("Meet at the Morena's court, it's said.")),
                         "Meet at the Morena's court, it's said.")

    def test_watching_a_pdf_link(self):
        pdf = make_pdf(["Notice No. 27: land held by families shall remain with families, says the Minister of Local Government today."] * 2)
        response = mock.Mock(status_code=200, headers={"Content-Type": "application/pdf"}, content=pdf)
        with mock.patch.object(sources, "_get", return_value=response):
            self.assertIn("shall remain with families", sources.fetch_page("https://gov.example.ls/gazette.pdf")["text"])
        scanned = make_pdf(["", ""])
        response = mock.Mock(status_code=200, headers={"Content-Type": "application/pdf"}, content=scanned)
        with mock.patch.object(sources, "_get", return_value=response):
            text = sources.fetch_page("https://gov.example.ls/scan.pdf")["text"]
        self.assertIn("Scanned PDF (2 pages)", text)
        self.assertIn("fingerprint", text)

    def test_unreadable_books_explain_why(self):
        from tripwire import books
        with self.assertRaises(books.BookError):
            books.read_book("empty.txt", b"")
        with self.assertRaises(books.BookError) as caught:
            books.read_book("scan.pdf", make_pdf(["", ""]))
        self.assertIn("scanned", str(caught.exception))
        self.assertIn("on your own device", str(caught.exception))
        with self.assertRaises(books.BookError):
            books.read_book("broken.epub", b"PK not really a zip")

    def test_line_breaks_and_hyphens_dont_count_as_changes(self):
        a = "The elders of the village gathered by the river. They sang the old har-\nvest song together."
        b = "The elders of the village\ngathered by the river.\nThey sang the old harvest\nsong together."
        self.assertEqual(changes.compare_long(a, b)["hunks"], [])

    def test_word_swaps_are_counted(self):
        result = changes.compare_long(OLD_BOOK, NEW_BOOK)
        swaps = changes.swap_counts(result["replacements"])
        self.assertEqual(swaps[0], ("killed", "unalived", 6))
        self.assertIn(("Loompaland", "Lumpaland"), result["name_changes"])


GUTENBERG_HEADER = """The Project Gutenberg eBook of Alice's Adventures in Wonderland

Title: Alice's Adventures in Wonderland
Author: Lewis Carroll
Release date: {released}
Language: English
Original publication: London: Macmillan, 1865

*** START OF THE PROJECT GUTENBERG EBOOK ALICE'S ADVENTURES IN WONDERLAND ***
"""
GUTENBERG_FOOTER = """
*** END OF THE PROJECT GUTENBERG EBOOK ALICE'S ADVENTURES IN WONDERLAND ***

Updated editions will replace the previous one. Licence version {released}.
"""


class BookPageTests(CheckerTests):
    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        facts = mock.patch.object(sources, "open_library_facts", lambda *a: {
            "url": "https://openlibrary.org/works/OL1W", "first_published": 1964, "editions": 212,
            "typical_pages": 176, "subjects": ["Chocolate", "Factories"]})
        facts.start()
        self.addCleanup(facts.stop)

    def compare(self, data, run=True):
        """Submit the form like the page does, then run the job like the waiting page does."""
        response = self.client.post("/books", data=data, content_type="multipart/form-data")
        if response.status_code != 302 or "/books/jobs/" not in response.headers["Location"]:
            return None, response
        job_page = response.headers["Location"]
        if not run:
            return job_page, response
        result = self.client.post(job_page + "/run").json
        return result, self.client.get(result["url"]) if result.get("url") else self.client.get(job_page)

    def test_compare_uploaded_editions(self):
        import io
        unchanged = " ".join(f"Chapter {i} tells of the river and the old stone house." for i in range(2000))
        result, response = self.compare({
            "title": "Chocolate",
            "file_a": (io.BytesIO(make_epub([OLD_BOOK, unchanged])), "dahl-1998.epub"),  # read in reverse: unchanged first
            "file_b": (io.BytesIO((unchanged + " " + NEW_BOOK).encode()), "dahl-2023.txt"),
        })
        self.assertEqual(result["status"], "done")
        page = response.data.decode()
        for expected in ["Word swaps", "unalived", "Lumpaland", "dahl-1998.epub (EPUB", "What kind of changes?",
                         "Most interesting changes", "Book facts", "Editions recorded", "212"]:
            self.assertIn(expected, page)
        self.assertIn("Chocolate", self.client.get("/books").data.decode())
        stored = self.tw.conn.execute("SELECT details FROM book_comparisons").fetchone()["details"]
        self.assertLess(len(stored), len(unchanged) / 10)  # only the changes are stored, not the books
        self.assertNotIn("Chapter 1999", stored)

    def test_waiting_page_then_result(self):
        import io
        job_page, _ = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                    "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")}, run=False)
        page = self.client.get(job_page).data.decode()
        self.assertIn("Reviewing books takes a while", page)
        self.assertIn("you can close this page", page)
        self.assertEqual(self.client.get(job_page + "/status").json["status"], "waiting")
        self.assertIn("In progress", self.client.get("/books").data.decode())
        self.client.post(job_page + "/run")
        self.assertEqual(self.client.get(job_page).status_code, 302)  # done: goes straight to the result

    def test_compare_from_links(self):
        from tripwire import books
        files = {"https://example.org/a.txt": ("a.txt", OLD_BOOK.encode()),
                 "https://example.org/b.pdf": ("b.pdf", make_pdf([NEW_BOOK[:100], NEW_BOOK[100:200]]))}
        with mock.patch.object(books, "fetch_book", lambda url: files[url]):
            result, response = self.compare({"link_a": "https://example.org/a.txt",
                                             "link_b": "https://example.org/b.pdf"})
        self.assertIn("b.pdf (PDF, 2 pages", response.data.decode())

    def test_gutenberg_licence_text_is_ignored(self):
        import io
        a = GUTENBERG_HEADER.format(released="June 25, 2008 [eBook #11]") + OLD_BOOK + GUTENBERG_FOOTER.format(released="2008")
        b = GUTENBERG_HEADER.format(released="October 1, 2024 [eBook #11]") + OLD_BOOK + GUTENBERG_FOOTER.format(released="2024")
        result, response = self.compare({"file_a": (io.BytesIO(a.encode()), "pg11-2008.txt"),
                                         "file_b": (io.BytesIO(b.encode()), "pg11-2024.txt")})
        page = response.data.decode()
        self.assertIn("No differences", page)  # only the licence text differed
        for fact in ["Lewis Carroll", "1865", "London: Macmillan", "Project Gutenberg", "October 1, 2024"]:
            self.assertIn(fact, page)

    def test_missing_edition_is_explained(self):
        import io
        _, response = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt")})
        self.assertIn("Add edition B", response.data.decode())

    def test_unreachable_link_is_explained_plainly(self):
        with mock.patch.object(sources.requests, "get", side_effect=sources.requests.ConnectionError("proxy 403 gibberish")):
            result, response = self.compare({"link_a": "https://example.org/a.txt", "link_b": "https://example.org/b.txt"})
        self.assertEqual(result["status"], "failed")
        page = response.data.decode()
        self.assertIn("Couldn&#39;t reach example.org. Check the link", page)
        self.assertNotIn("gibberish", page)

    def test_job_runs_only_once_and_survives_closing_the_page(self):
        import io
        job_page, _ = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                    "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")}, run=False)
        job_id = int(job_page.rsplit("/", 1)[1])
        # The page was closed before it started the job: the daily run picks it up.
        with mock.patch.dict(os.environ, {"CRON_SECRET": "s3cret"}):
            self.assertEqual(self.client.get("/cron", headers={"Authorization": "Bearer s3cret"}).json["book_jobs_run"], 1)
        self.assertEqual(self.tw.run_book_job(job_id)["status"], "done")  # running again does nothing new
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM book_comparisons").fetchone()["n"], 1)

    def test_interrupted_job_is_retried(self):
        import io
        job_page, _ = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                    "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")}, run=False)
        job_id = int(job_page.rsplit("/", 1)[1])
        self.tw.conn.execute("UPDATE book_jobs SET status = 'running', started_at = '2026-01-01T00:00:00Z'")
        self.assertEqual([j["id"] for j in self.tw.unfinished_book_jobs()], [job_id])
        self.assertEqual(self.tw.run_book_job(job_id)["status"], "done")

    def test_book_files_deleted_after_seven_days(self):
        import io
        result, _ = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                  "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")})
        self.tw.delete_book_files(older_than_days=7)
        self.assertIsNotNone(self.tw.conn.execute("SELECT file_a FROM book_jobs").fetchone()["file_a"])  # still new
        self.tw.conn.execute("UPDATE book_jobs SET created_at = '2026-01-01T00:00:00Z'")
        self.client.get("/books")  # visiting Books tidies up
        job = self.tw.conn.execute("SELECT file_a, file_b, files_deleted_at FROM book_jobs").fetchone()
        self.assertEqual((job["file_a"], job["file_b"]), (None, None))
        page = self.client.get(result["url"]).data.decode()
        self.assertIn("Word swaps", page)  # the comparison stays
        self.assertIn("Deleted on", page)

    def test_delete_files_now(self):
        import io
        result, response = self.compare({"file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                         "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")})
        self.assertIn("Delete the files now", response.data.decode())
        comparison_id = int(result["url"].rsplit("/", 1)[1])
        self.client.post(f"/books/{comparison_id}/files/delete")
        self.assertIsNone(self.tw.conn.execute("SELECT file_a FROM book_jobs").fetchone()["file_a"])

    def test_comparisons_are_private(self):
        import io
        result, _ = self.compare({"title": "Secret comparison",
                                  "file_a": (io.BytesIO(OLD_BOOK.encode()), "a.txt"),
                                  "file_b": (io.BytesIO(NEW_BOOK.encode()), "b.txt")})
        comparison_url = result["url"]
        self.tw.create_user("stranger@example.org", "stranger danger")
        self.client.post("/logout")
        self.client.post("/login", data={"email": "stranger@example.org", "password": "stranger danger"})
        self.assertEqual(self.client.get(comparison_url).status_code, 404)
        self.assertEqual(self.client.post(comparison_url + "/delete").status_code, 404)
        self.assertEqual(self.client.post(comparison_url + "/files/delete").status_code, 404)
        for path in ["/books/jobs/1", "/books/jobs/1/status"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/books/jobs/1/run").status_code, 404)
        self.assertNotIn("Secret comparison", self.client.get("/books").data.decode())


class ChangeKindTests(unittest.TestCase):
    def test_kinds(self):
        cases = {("“Hello”", '"hello"'): "formatting", ("Hello,", "Hello;"): "grammar", ("colour", "color"): "spelling",
                 ("Loompaland", "Lumpaland"): "names", ("the", "a"): "grammar", ("killed", "unalived"): "swap",
                 ("He walked home in the rain that night.", ""): "content", ("", "A brand new paragraph here."): "content"}
        for (old, new), kind in cases.items():
            self.assertEqual(changes.classify(old, new), kind, (old, new))

    def test_donut_adds_up(self):
        from tripwire import charts
        chart = charts.donut({"spelling": 3, "names": 1, "grammar": 0, "formatting": 0, "swap": 4, "content": 2},
                             changes.CHANGE_KINDS)
        self.assertEqual(chart["total"], 10)
        self.assertAlmostEqual(sum(s["percent"] for s in chart["slices"]), 100)
        self.assertEqual([s["percent_text"] for s in chart["slices"]], ["30%", "10%", "0%", "0%", "40%", "20%"])
        self.assertEqual(charts.donut(dict.fromkeys(changes.CHANGE_KINDS, 0), changes.CHANGE_KINDS)["total"], 0)

    def test_epub_details_are_read(self):
        import io
        import zipfile
        from tripwire import books
        data = make_epub([OLD_BOOK])
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as dst:
            for item in src.infolist():
                body = src.read(item.filename)
                if item.filename.endswith(".opf"):
                    body = body.replace(b"<manifest>", b'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                                        b"<dc:title>Charlie</dc:title><dc:creator>Roald Dahl</dc:creator>"
                                        b"<dc:publisher>Puffin</dc:publisher><dc:date>2023-02-01</dc:date>"
                                        b"<dc:identifier>urn:isbn:978-0-14-137100-0</dc:identifier></metadata><manifest>")
                dst.writestr(item, body)
        _, info = books.read_book("charlie.epub", buf.getvalue())
        self.assertEqual((info["title"], info["authors"], info["publisher"], info["year"], info["isbn"]),
                         ("Charlie", "Roald Dahl", "Puffin", "2023", "9780141371000"))
        self.assertEqual(info["pages_estimate"], 1)


def fake_video_site(oembed_status=200, title="Warli painting: a living tradition", author="Goa Stories",
                    player=None, tiktok_status=200):
    """Stands in for YouTube and TikTok. `player` is what YouTube puts in the video page."""
    import json as _json

    def fake_get(url, **kwargs):
        response = mock.Mock()
        if "oembed" in url and "tiktok" in url:
            response.status_code = tiktok_status
            response.json.return_value = {"title": title, "author_name": author}
        elif "oembed" in url:
            response.status_code = oembed_status
            response.json.return_value = {"title": title, "author_name": author}
        else:
            response.status_code = 200
            response.text = ("<html><script>var ytInitialPlayerResponse = " + _json.dumps(player) + ";var x = 1;</script></html>"
                             if player is not None else "<html>no player here</html>")
        return response
    return fake_get


def youtube_player(status="OK", reason=None, family_safe=True, unlisted=False, title="Warli painting: a living tradition"):
    playability = {"status": status}
    if reason:
        playability["reason"] = reason
    return {"playabilityStatus": playability, "videoDetails": {"title": title, "author": "Goa Stories"},
            "microformat": {"playerMicroformatRenderer": {"isFamilySafe": family_safe, "isUnlisted": unlisted}}}


YOUTUBE_LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class VideoSourceTests(unittest.TestCase):
    def status(self, **site):
        with mock.patch.object(sources, "_get", fake_video_site(**site)):
            return sources.video_status(YOUTUBE_LINK)

    def test_recognises_video_links(self):
        for link in [YOUTUBE_LINK, "https://youtu.be/dQw4w9WgXcQ", "youtube.com/shorts/dQw4w9WgXcQ",
                     "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ"]:
            self.assertEqual(sources.video_platform(link), "YouTube", link)
        self.assertEqual(sources.video_platform("https://www.tiktok.com/@goa/video/7300000000000000000"), "TikTok")
        self.assertIsNone(sources.video_platform("https://example.org/watch?v=dQw4w9WgXcQ"))
        self.assertIsNone(sources.video_platform("https://en.wikipedia.org/wiki/Warli"))

    def test_normal_video(self):
        st = self.status(player=youtube_player())
        self.assertEqual((st["available"], st["restricted"], st["unlisted"]), (True, False, False))
        self.assertEqual(st["author"], "Goa Stories")

    def test_age_restricted(self):
        st = self.status(player=youtube_player("LOGIN_REQUIRED", "Sign in to confirm your age", family_safe=False))
        self.assertEqual((st["available"], st["restricted"]), (True, True))

    def test_removed_with_reason(self):
        st = self.status(oembed_status=404, player={"playabilityStatus": {"status": "ERROR", "errorScreen": {
            "playerErrorMessageRenderer": {"reason": {"runs": [{"text": "This video has been removed "},
                                                               {"text": "by the uploader"}]}}}}})
        self.assertFalse(st["available"])
        self.assertEqual(st["reason"], "This video has been removed by the uploader")

    def test_private(self):
        st = self.status(oembed_status=401, player=youtube_player("LOGIN_REQUIRED", "This video is private"))
        self.assertEqual((st["available"], st["reason"]), (False, "This video is private"))

    def test_human_check_is_not_mistaken_for_removal(self):
        st = self.status(player=youtube_player("LOGIN_REQUIRED", "Sign in to confirm you’re not a bot"))
        self.assertTrue(st["available"])

    def test_page_unreadable_falls_back_to_oembed(self):
        st = self.status(player=None)
        self.assertEqual((st["available"], st["title"], st["restricted"]), (True, "Warli painting: a living tradition", None))
        st = self.status(oembed_status=401, player=None)
        self.assertFalse(st["available"])

    def test_busy_site_is_not_a_change(self):
        with self.assertRaises(sources.SourceError):
            self.status(oembed_status=429)

    def test_tiktok(self):
        link = "https://www.tiktok.com/@goa/video/7300000000000000000"
        with mock.patch.object(sources, "_get", fake_video_site(title="Konkani lullaby")):
            st = sources.video_status(link)
        self.assertEqual((st["available"], st["title"], st["restricted"]), (True, "Konkani lullaby", None))
        with mock.patch.object(sources, "_get", fake_video_site(tiktok_status=400)):
            self.assertFalse(sources.video_status(link)["available"])


class VideoWatchTests(CheckerTests):
    def check(self, watch, **site):
        with mock.patch.object(sources, "_get", fake_video_site(**site)):
            return self.tw.check_watch(watch)

    def add(self, **site):
        with mock.patch.object(sources, "_get", fake_video_site(**site)):
            return self.tw.add_video(self.user, YOUTUBE_LINK)

    def test_retitled_then_restricted_then_removed(self):
        watch = self.add(player=youtube_player())
        self.assertIsNone(self.check(watch, player=youtube_player()))
        self.check(watch, player=youtube_player(title="Warli art (edited)"), title="Warli art (edited)")
        self.check(watch, player=youtube_player("LOGIN_REQUIRED", "Sign in to confirm your age", family_safe=False,
                                                title="Warli art (edited)"), title="Warli art (edited)")
        self.check(watch, oembed_status=404, player={"playabilityStatus": {"status": "ERROR", "reason": "Video unavailable"}})
        entries = self.entries()
        self.assertEqual([e["category"] for e in entries], ["video_retitled", "video_restricted", "video_gone"])
        self.assertIn("“Warli painting: a living tradition” → “Warli art (edited)”", entries[0]["summary"])
        self.assertIn("Available: no (Video unavailable)", entries[2]["after_text"])
        watch_row = self.tw.own_watch(self.user, watch)
        self.assertEqual(watch_row["last_status"], "Unavailable")
        self.assertEqual(self.tw.copy_options(watch=watch_row), [])
        self.assertEqual(self.tw.copy_options(entry=self.tw.own_entry(self.user, entries[2]["id"])), [])
        self.assertIsNone(self.check(watch, oembed_status=404, player=None))  # still gone: not reported twice

    def test_unknown_details_dont_cause_false_alarms(self):
        watch = self.add(player=youtube_player())
        self.assertIsNone(self.check(watch, player=None))  # page unreadable this time
        state = self.tw.own_watch(self.user, watch)["baseline_ref"]
        self.assertIn('"restricted": false', state)  # remembered from before

    def test_watched_videos_are_on_the_watching_page(self):
        self.add(player=youtube_player())
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        client = webapp.app.test_client()
        client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        page = client.get("/watching").data.decode()
        self.assertIn('id="videos"', page)
        self.assertIn("Warli painting: a living tradition", page)
        self.assertIn("Available", page)
        self.assertNotIn("Test against Internet Archive", page.split('id="videos"')[1])  # videos aren't pages to compare

    def test_check_a_video_without_watching_it(self):
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        client = webapp.app.test_client()
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Konkani lullaby"), title="Konkani lullaby")):
            page = client.post("/videos/check", data={"target": "https://www.youtube.com/watch?v=aaaaaaaaaaa"}).data.decode()
        self.assertIn("Konkani lullaby", page)
        self.assertIn("checked just now", page)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM watches WHERE kind = 'video'").fetchone()["n"], 0)
        self.assertIn("Paste a YouTube or TikTok video link.",
                      client.post("/videos/check", data={"target": "https://example.org/"}, follow_redirects=True).data.decode())
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Second video"), title="Second video")):
            response = client.post("/single-links", data={"target": "https://youtu.be/aaaaaaaaaaa"}, follow_redirects=True)
        self.assertIn("Now watching that video", response.data.decode())  # pasted in the wrong place, still works
        self.assertIn("Second video", client.get("/watching").data.decode())


def fake_cloudflare(status=200, human="48.7", bot="51.3"):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        response = mock.Mock(status_code=status)
        if "summary" in url:
            response.json.return_value = {"success": True, "result": {"summary_0": {"human": human, "bot": bot}}}
        else:
            response.json.return_value = {"success": True, "result": {"serie_0": {
                "timestamps": ["2026-10-27T00:00:00Z", "2026-10-27T01:00:00Z", "2026-10-28T00:00:00Z"],
                "human": ["50.1", "49.0", "48.7"], "bot": ["49.9", "51.0", "51.3"]}}}
        return response
    return fake_get, calls


def fake_year(year):
    """Pretend Open Library: more books every year, digital copies taking off after 2000."""
    if year == "before":
        return {"all": 5000, "digital": 4000}
    return {"all": 1000 + (year - 1900) * 10 - (500 if year == 1944 else 0), "digital": max(0, (year - 2000) * 50) + 5}


class StatsTests(CheckerTests):
    def stats(self):
        from tripwire import stats
        return stats.Stats(self.tw.conn)

    def test_traffic_needs_a_token(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLOUDFLARE_API_TOKEN", None)
            self.assertEqual(self.stats().traffic(), {"connected": False})

    def test_traffic_from_cloudflare_is_cached(self):
        fake, calls = fake_cloudflare()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t"}), mock.patch.object(sources, "_get", fake):
            t = self.stats().traffic()
            self.assertEqual((t["human"], t["bot"]), (48.7, 51.3))
            self.assertEqual(t["series"]["bot"], [49.9, 51.0, 51.3])
            self.stats().traffic()
        self.assertEqual(len(calls), 2)  # the second visit used the remembered numbers

    def test_bad_token_is_explained(self):
        fake, _ = fake_cloudflare(status=403)
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t"}), mock.patch.object(sources, "_get", fake):
            self.assertIn("didn't accept the API token", self.stats().traffic()["error"])

    def test_book_running_totals_only_go_up(self):
        from tripwire import stats
        with mock.patch.object(stats, "_open_library_year", fake_year):
            missing, total, problem = self.stats().fill_book_years(seconds=30)
        self.assertEqual(missing, 0)
        b = self.stats().books()
        for key in ("all", "print", "digital"):
            values = b["running"][key]
            self.assertEqual(values, sorted(values), key)  # never decreases
        self.assertEqual(b["running"]["all"][0], 5000 + 1000)
        self.assertEqual(b["peaks"]["digital"]["year"], b["last_year"])
        self.assertEqual(b["lows"]["print"]["year"], 1944)
        self.assertEqual(b["totals"]["all"], b["totals"]["print"] + b["totals"]["digital"])

    def test_world_estimate_starts_at_googles_count(self):
        from tripwire import stats
        w = stats.Stats.world_estimate()
        self.assertEqual(w["years"][0], 2010)  # starts at Google's count
        self.assertEqual(w["mid"][0], 129_864_880)
        self.assertEqual(w["low"][1], 129_864_880 + 2_200_000)
        self.assertEqual(w["high"][1], 129_864_880 + 4_000_000)
        self.assertEqual(w["mid"], sorted(w["mid"]))  # only goes up
        self.assertEqual(w["now"]["year"], w["years"][-1])

    def test_backs_off_when_open_library_is_busy(self):
        from tripwire import stats
        calls = []

        def busy(year):
            calls.append(year)
            return "Open Library is busy (too many requests), so Tripwire is slowing down."
        with mock.patch.object(stats, "_open_library_year", busy):
            missing, total, problem = self.stats().fill_book_years(seconds=30)
        self.assertIn("busy", problem)
        self.assertEqual(missing, total)
        self.assertLessEqual(len(calls), 3)  # stopped after one small batch

    def test_book_years_fetched_a_few_at_a_time(self):
        from tripwire import stats
        with mock.patch.object(stats, "_open_library_year", fake_year):
            missing, total, problem = self.stats().fill_book_years(seconds=0)
        self.assertGreater(missing, 0)
        self.assertLess(missing, total)


class StatsPageTests(CheckerTests):
    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})

    def test_page_before_anything_is_connected(self):
        os.environ.pop("CLOUDFLARE_API_TOKEN", None)
        page = self.client.get("/stats").data.decode()
        self.assertIn("Not connected yet", page)
        self.assertIn("Collecting book counts from Open Library: 0 of", page)
        self.assertIn("The whole world (estimate)", page)  # shown even before Open Library's numbers arrive
        self.assertIn("No changes caught yet", page)

    def test_page_with_everything(self):
        from tripwire import stats
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        self.tw.check_watch(watch)
        with mock.patch.object(stats, "_open_library_year", fake_year):
            self.assertEqual(self.client.post("/stats/books/fill").json["missing"], 0)
        fake, _ = fake_cloudflare()
        with mock.patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "t"}), mock.patch.object(sources, "_get", fake):
            page = self.client.get("/stats").data.decode()
            live = self.client.get("/stats/traffic.json").json
        for expected in ["48.7%", "51.3%", "The last 7 days", "Peak year, digital", "Lowest year, print only",
                         "1944", "Changes by kind", "Changes per week", "Tiny &amp; formatting",
                         "Google's count, 2010", "129,864,880", "World estimate", "Lesotho"]:
            self.assertIn(expected, page)
        self.assertNotIn('id="books-progress"', page)  # the progress message is gone
        self.assertEqual(live["human"], 48.7)

    def test_menu(self):
        page = self.client.get("/stats").data.decode()
        nav = page.split('<nav class="nav-wide"')[1].split("</nav>")[0]
        self.assertEqual([label for label in ["Watching", "My shelf", "Changes", "Books &amp; videos", "About"] if label in nav],
                         ["Watching", "My shelf", "Changes", "Books &amp; videos", "About"])
        for gone in ["Stats", "Then and now", "Live watch", "Archive", "Summary", ">More<"]:
            self.assertNotIn(gone, nav)
        self.assertIn('<p class="footer-stats"><a href="/stats">Stats</a>', page)  # at the bottom of every page


class FakeSMTP:
    """Stands in for an email server; remembers what was sent."""
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def starttls(self):
        pass

    def login(self, user, password):
        if password == "wrong":
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b"bad login")

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


EMAIL_SETTINGS = {"SMTP_HOST": "smtp.example.org", "SMTP_PORT": "587", "SMTP_USER": "tripwire",
                  "SMTP_PASSWORD": "secret", "MAIL_FROM": "Tripwire <hello@example.org>"}


class EmailAndAboutTests(CheckerTests):
    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        self.tw.confirm_email(self.user, "purple@example.org")  # most email tests start with a confirmed address
        FakeSMTP.sent = []
        smtp = mock.patch("smtplib.SMTP", FakeSMTP)
        smtp.start()
        self.addCleanup(smtp.stop)

    def make_a_change(self):
        watch = self.tw.add_watch(self.theme, "Warli")
        self.add_version(2, "The Warlee are an Adivasi people of Maharashtra.")
        self.tw.check_watch(watch)

    def test_email_not_set_up(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for key in EMAIL_SETTINGS:
                os.environ.pop(key, None)
            self.assertIn("Email isn't set up on this site yet", self.client.get("/changes").data.decode())
            response = self.client.post("/summary/send", follow_redirects=True)
        self.assertIn("Email isn&#39;t set up on this site yet", response.data.decode())
        self.assertEqual(FakeSMTP.sent, [])

    def test_send_summary_now(self):
        self.make_a_change()
        with mock.patch.dict(os.environ, EMAIL_SETTINGS):
            page = self.client.post("/summary/send", follow_redirects=True).data.decode()
            self.assertIn("Sent to purple@example.org", page)
            again = self.client.post("/summary/send", follow_redirects=True).data.decode()
        self.assertIn("One just went out", again)  # not twice in a row
        [message] = FakeSMTP.sent
        self.assertEqual(message["To"], "purple@example.org")
        self.assertEqual(message["Subject"], "Tripwire: 1 change this week")
        text = message.get_body(("plain",)).get_content()
        html = message.get_body(("html",)).get_content()
        self.assertIn("Warli → Warlee", text)
        self.assertIn("http://localhost/entries/1", html)
        self.assertIn("Switch them off", html)

    def test_bad_email_login_is_explained(self):
        with mock.patch.dict(os.environ, dict(EMAIL_SETTINGS, SMTP_PASSWORD="wrong")):
            page = self.client.post("/summary/send", follow_redirects=True).data.decode()
        self.assertIn("didn&#39;t accept the login", page)

    def test_weekly_emails_go_out_on_mondays_once(self):
        self.make_a_change()
        other = self.tw.create_user("quiet@example.org", "another password")
        self.tw.set_weekly_email(other, False)  # switched off
        monday, tuesday = time.struct_time((2026, 11, 2, 3, 17, 0, 0, 306, 0)), time.struct_time((2026, 11, 3, 3, 17, 0, 1, 307, 0))
        env = dict(EMAIL_SETTINGS, CRON_SECRET="s3cret")
        with mock.patch.dict(os.environ, env):
            with mock.patch("time.gmtime", side_effect=lambda *a: tuesday if not a else time.struct_time(time.localtime(a[0]))):
                self.assertEqual(self.client.get("/cron", headers={"Authorization": "Bearer s3cret"}).json["summaries_emailed"], 0)
            with mock.patch("time.gmtime", side_effect=lambda *a: monday if not a else time.struct_time(time.localtime(a[0]))):
                self.assertEqual(self.client.get("/cron", headers={"Authorization": "Bearer s3cret"}).json["summaries_emailed"], 1)
                self.assertEqual(self.client.get("/cron", headers={"Authorization": "Bearer s3cret"}).json["summaries_emailed"], 0)
        self.assertEqual([m["To"] for m in FakeSMTP.sent], ["purple@example.org"])

    def test_choose_daily_weekly_monthly_or_off(self):
        monday_1st = time.struct_time((2027, 2, 1, 3, 17, 0, 0, 32, 0))
        tuesday_2nd = time.struct_time((2027, 2, 2, 3, 17, 0, 1, 33, 0))

        def due_on(day):
            with mock.patch("time.gmtime", side_effect=lambda *a: day if not a else time.struct_time(time.localtime(a[0]))):
                return len(self.tw.users_due_summary())

        with mock.patch.dict(os.environ, EMAIL_SETTINGS):
            self.assertIn('value="weekly" checked', self.client.get("/changes").data.decode())  # the starting choice
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (1, 0))
            self.client.post("/summary/email", data={"every": "off"})
            self.assertIn('value="off" checked', self.client.get("/changes").data.decode())
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (0, 0))
            self.client.post("/summary/email", data={"every": "daily"})
            page = self.client.get("/changes").data.decode()
            self.assertIn("Daily summary", page)
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (1, 1))
            self.client.post("/summary/email", data={"every": "monthly"})
            self.assertIn("Monthly summary", self.client.get("/changes").data.decode())
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (1, 0))
            self.assertIn("Choose daily", self.client.post("/summary/email", data={"every": "hourly"},
                                                           follow_redirects=True).data.decode())

    def test_daily_email_says_today(self):
        self.make_a_change()
        self.tw.set_summary_every(self.user, "daily")
        with mock.patch.dict(os.environ, EMAIL_SETTINGS):
            self.client.post("/summary/send")
        self.assertEqual(FakeSMTP.sent[-1]["Subject"], "Tripwire: 1 change today")
        body = FakeSMTP.sent[-1].get_body(("plain",)).get_content()
        self.assertIn("TRIPWIRE · DAILY SUMMARY", body)
        self.assertIn("You get this every day", body)

    def test_nothing_is_emailed_until_the_address_is_confirmed(self):
        import app as webapp
        self.make_a_change()
        stranger = self.tw.create_user("stranger@example.org", "a long password")
        with mock.patch.dict(os.environ, EMAIL_SETTINGS):
            monday = time.struct_time((2026, 11, 2, 3, 17, 0, 0, 306, 0))
            with mock.patch("time.gmtime", side_effect=lambda *a: monday if not a else time.struct_time(time.localtime(a[0]))):
                self.assertNotIn(stranger, [u["id"] for u in self.tw.users_due_summary()])  # never confirmed
            # Signing up sends one "confirm your email" message, and no more for a while.
            visitor = webapp.app.test_client()
            visitor.post("/login", data={"email": "stranger@example.org", "password": "a long password"})
            self.assertIn("First, confirm your email address", visitor.get("/changes").data.decode())
            self.assertIn("confirm your email", visitor.post("/summary/send", follow_redirects=True).data.decode())
            visitor.post("/confirm/send")
            visitor.post("/confirm/send")
            confirmations = [m for m in FakeSMTP.sent if m["To"] == "stranger@example.org"]
            self.assertEqual(len(confirmations), 1)
            link = re.search(r"https?://\S+/confirm/\S+", confirmations[0].get_body(("plain",)).get_content()).group(0)
            path = link.split("localhost", 1)[1]
            # Opening the link only asks; nothing is confirmed until the button is pressed.
            self.assertIn("Yes, confirm it", visitor.get(path).data.decode())
            self.assertIsNone(self.tw.user(stranger)["email_confirmed_at"])
            visitor.post(path)
            self.assertIsNotNone(self.tw.user(stranger)["email_confirmed_at"])
            self.assertIn("That link doesn't work", visitor.get(path[:-3] + "xyz").data.decode())  # a tampered link

    def test_older_accounts_keep_their_weekly_switch(self):
        self.tw.conn.execute("UPDATE users SET summary_every = NULL, weekly_email = 0")
        self.assertEqual(Tripwire.summary_every(self.tw.user(self.user)), "off")
        self.tw.conn.execute("UPDATE users SET weekly_email = 1")
        self.assertEqual(Tripwire.summary_every(self.tw.user(self.user)), "weekly")

    def test_about_page_is_public(self):
        self.client.post("/logout")
        page = self.client.get("/about").data.decode()
        for expected in ["Who it&#39;s for", "No AI inside, on purpose", "How Tripwire changed", "So far",
                         "Purple, Tripwire&#39;s maker", "Send feedback", "A sign-up wall → try it first"]:
            self.assertIn(expected, page.replace("Who it's for", "Who it&#39;s for").replace("Purple, Tripwire's maker", "Purple, Tripwire&#39;s maker"))
        self.assertIn("Add the examples", page)  # no account needed to try the examples
        self.assertIn("[5] ABOUT", self.client.get("/").data.decode())

    def test_feedback_reaches_only_the_owner(self):
        self.client.post("/feedback", data={"tried": "Books", "message": "Loved the Frankenstein comparison!"})
        self.assertEqual(self.tw.totals()["feedback"], 1)
        self.assertEqual(self.client.get("/feedback").status_code, 404)  # not the owner
        self.assertNotIn("Feedback inbox", self.client.get("/about").data.decode())
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "someone@else.org, Purple@Example.org"}):
            page = self.client.get("/feedback").data.decode()
            self.assertIn("Loved the Frankenstein comparison!", page)
            self.assertIn("Feedback inbox", self.client.get("/about").data.decode())
        self.client.post("/logout")
        self.client.post("/feedback", data={"message": "spam", "website": "http://spam"})  # a bot: ignored
        self.assertEqual(self.tw.totals()["feedback"], 1)
        self.client.post("/feedback", data={"tried": "Stats", "message": "No account, still helpful"})
        self.assertEqual(self.tw.totals()["feedback"], 2)  # visitors can leave feedback too
        self.assertEqual(self.tw.totals()["people"], 1)  # without becoming a guest

    def test_demo_data(self):
        FakeWikipedia.articles = {title: [{"revid": n * 10 + 1, "user": "A", "comment": "", "text": f"About {title}. " * 20},
                                          {"revid": n * 10 + 2, "user": "B", "comment": "update", "text": f"About {title}, rewritten. " * 20}]
                                  for n, title in enumerate(sum(self.tw.DEMO_THEMES.values(), []) + self.tw.DEMO_SINGLE_LINKS)}
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Me at the zoo"))):
            response = self.client.post("/demo-data")
        self.assertTrue(response.headers["Location"].endswith("/books#books"))  # both book comparisons start there
        totals = self.tw.totals()
        self.assertEqual(totals["watched"], 6)  # two articles per theme, one single link, one video
        self.assertEqual(totals["changes"], 5)
        for theme in self.tw.DEMO_THEMES.values():
            self.assertEqual(len(theme), 2)  # never more than two per section
        jobs = self.tw.conn.execute("SELECT title, link_a, link_b FROM book_jobs ORDER BY id").fetchall()
        self.assertEqual(len(jobs), 2)
        self.assertIn("Frankenstein", jobs[0]["title"])
        self.assertIn("41445", jobs[0]["link_a"])
        self.assertIn("Origin of Species", jobs[1]["title"])
        self.assertTrue(jobs[1]["link_a"].endswith("/pg1228.txt") and jobs[1]["link_b"].endswith("/pg2009.txt"))
        # Pressing it again adds nothing new.
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Me at the zoo"))):
            self.client.post("/demo-data")
        self.assertEqual(self.tw.totals()["watched"], 6)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM book_jobs").fetchone()["n"], 2)
        # Only this account was touched.
        other = self.tw.create_user("other@example.org", "another password")
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM themes WHERE user_id = ?", (other,)).fetchone()["n"], 0)


class LiveWatchTests(CheckerTests):
    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        self.pages = {"https://news.example.org/live": {"status": "ok", "text": "Live: election day\nPolls opened at 7am. Updated 2 minutes ago"}}
        fetch = mock.patch.object(sources, "fetch_page", lambda url: self.pages[url])
        fetch.start()
        self.addCleanup(fetch.stop)

    def live_check(self, previous=None):
        with self.client.session_transaction() as sess:
            sess.pop("live_last_check", None)  # pretend a minute has passed
        return self.client.post("/live/check", json={"url": "news.example.org/live", "previous": previous}).json

    def test_noise_is_ignored(self):
        from tripwire import live
        self.assertEqual(live.steady_text("Breaking\nPosted 5 minutes ago\n12,301 people watching now\nUpdated just now"),
                         "Breaking\nPosted")

    def test_live_updates(self):
        first = self.live_check()
        self.assertFalse(first["changed"])
        self.pages["https://news.example.org/live"]["text"] = "Live: election day\nPolls opened at 7am. Updated 9 minutes ago"
        self.assertFalse(self.live_check(first["text"])["changed"])  # only the "ago" changed
        self.pages["https://news.example.org/live"]["text"] = ("Live: election day\nTurnout is high in Maseru.\n"
                                                              "Polls opened at 7am. Updated just now")
        update = self.live_check(first["text"])
        self.assertTrue(update["changed"])
        self.assertIn("Turnout is high in Maseru", update["hunks"][0]["added"])
        self.pages["https://news.example.org/live"] = {"status": "removed", "code": 404}
        self.assertTrue(self.live_check(update["text"])["gone"])

    def test_not_too_often(self):
        self.assertNotIn("error", self.client.post("/live/check", json={"url": "news.example.org/live"}).json)
        response = self.client.post("/live/check", json={"url": "news.example.org/live"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("too often", response.json["error"])

    def test_live_watch_is_a_switch_on_watching(self):
        page = self.client.get("/watching").data.decode()
        self.assertIn("Live watch", page)
        self.assertLess(page.index('id="live"'), page.index("What you're watching"))  # at the top
        self.assertIn('id="live-switch" aria-pressed="false"', page)  # off until switched on
        self.assertIn('<div id="live-panel" hidden>', page)
        self.assertIn('id="live-switch" aria-pressed="true"', self.client.get("/watching?live=1").data.decode())
        self.assertEqual(self.client.post("/live/check", json={"url": "https://youtu.be/aaaaaaaaaaa"}).status_code, 400)
        self.client.post("/logout")
        self.assertEqual(self.client.get("/watching?live=1").status_code, 200)  # no account needed


class AccessAndLanguageTests(CheckerTests):
    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()

    def test_access_tools_on_every_page(self):
        for path in ["/", "/login", "/about", "/no-such-page"]:
            page = self.client.get(path).data.decode()
            for expected in ['class="skip-link" href="#main"', 'id="main"', 'id="access-button"', "Welcome to Tripwire",
                             "Would you like Tripwire to read things aloud to you?", "Calm mode", "Strong contrast"]:
                self.assertIn(expected, page, (path, expected))
        self.assertIn("your device's own voice", self.client.get("/").data.decode())

    def test_first_time_tour(self):
        page = self.client.get("/").data.decode()
        self.assertIn('id="tour"', page)
        self.assertIn("tour.js", page)
        self.assertIn("data-tour-skip", page)  # it can always be skipped
        self.assertIn("data-tour-start", page)  # "Take the tour" at the bottom of every page shows it again
        steps = page.split("<template data-tour-steps>")[1].split("</template>")[0]
        self.assertEqual(steps.count("<section"), 4)  # short: four steps
        for link in ["/watching", "/shelf", "/changes", "/books"]:
            self.assertIn(f'a[href="{link}"]', steps)  # each step lights up its menu item
        self.assertIn('action="/demo-data"', page)  # the last step offers the real examples
        for path in ["/login", "/signup", "/verify"]:
            self.assertNotIn('id="tour"', self.client.get(path).data.decode(), path)  # not where it would get in the way

    def test_only_english_until_a_translation_is_checked(self):
        """The Hindi and Sesotho drafts aren't on the site until a native speaker has checked them."""
        page = self.client.get("/").data.decode()
        self.assertNotIn('class="languages"', page)
        self.assertNotIn("access-languages", page)
        self.assertEqual(self.client.get("/language/hi").status_code, 404)
        self.assertEqual(self.client.get("/language/st").status_code, 404)

    def checked_sesotho(self):
        from tripwire import i18n
        return mock.patch.dict(i18n.LANGUAGES, {"st": {
            "_language": "Sesotho", "_english_name": "Sesotho", "Watching": "Ho shebella", "LOG IN": "KENA",
            "language_notice": "Sesotho sena se ntse se ntlafatsoa."}})

    def test_a_checked_translation_appears_and_works(self):
        with self.checked_sesotho():
            page = self.client.get("/").data.decode()
            self.assertIn('lang="st"', page.split('class="languages"')[1])  # offered at the bottom of every page
            response = self.client.get("/language/st?next=/about")
            self.assertTrue(response.headers["Location"].endswith("/about"))
            page = self.client.get("/").data.decode()
            self.assertIn('<html lang="st">', page)
            self.assertIn("[L] KENA", page)
            self.assertIn("Sesotho sena se ntse se ntlafatsoa.", page)  # the language's own note
            self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
            self.assertIn("Ho shebella", self.client.get("/watching").data.decode())
            self.client.get("/language/en")
            self.assertIn("[1] WATCHING", self.client.get("/").data.decode())

    def test_language_switch_is_safe(self):
        with self.checked_sesotho():
            self.assertEqual(self.client.get("/language/xx").status_code, 404)
            self.assertTrue(self.client.get("/language/st?next=//evil.example").headers["Location"].endswith("/"))

    def test_every_translation_file_is_valid(self):
        from tripwire import i18n
        for code, data in i18n.LANGUAGES.items():
            if code != "en":
                self.assertTrue({"_language", "_english_name", "language_notice"} <= set(data), code)


KIWIX_CATALOG = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
  <entry><name>wikipedia_en_all</name><flavour>mini</flavour><dc:issued>2025-06-01T00:00:00Z</dc:issued>
    <link type="text/html" href="/content/wikipedia_en_all_mini_2025-06" /></entry>
  <entry><name>wikipedia_en_all</name><flavour>maxi</flavour><dc:issued>2024-01-15T00:00:00Z</dc:issued>
    <link type="text/html" href="/content/wikipedia_en_all_maxi_2024-01" />
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
          href="https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2024-01.zim.meta4" length="1" /></entry>
  <entry><name>wikipedia_en_all</name><flavour>maxi</flavour><dc:issued>2023-01-15T00:00:00Z</dc:issued>
    <link type="text/html" href="/content/wikipedia_en_all_maxi_2023-01" /></entry>
</feed>"""


def fake_response(status=200, text="", content=None, content_type="text/html", url="", data=None):
    return mock.Mock(status_code=status, text=text, content=content if content is not None else text.encode(),
                     headers={"Content-Type": content_type}, url=url, json=lambda: data or {}, history=[])


def fake_kiwix_and_archive(kiwix_article=None, archive_text=None, kiwix_raw=None):
    """A pretend library.kiwix.org and Internet Archive. `kiwix_raw` is what Kiwix's raw
    address sends instead of the article (a robot check, say)."""
    seen = []

    def get(url, params=None, **kwargs):
        seen.append(url)
        if url.endswith("/catalog/v2/entries"):
            name = params["name"]
            return fake_response(text=KIWIX_CATALOG if name == "wikipedia_en_all" else "<feed xmlns='http://www.w3.org/2005/Atom'/>",
                                 content_type="application/atom+xml")
        if "wikipedia_en_all_maxi_2024-01/" in url:
            if kiwix_raw is not None:
                return fake_response(text=kiwix_raw, url=url)
            if kiwix_article and url.endswith("/A/Warli"):
                return fake_response(text=kiwix_article, url=url)
            if kiwix_article and url.endswith("/A/Warli_people"):
                return fake_response(text=kiwix_article.replace("Warli", "Warli people"), url=url)
            return fake_response(status=404)
        if url.endswith("/suggest"):
            return fake_response(data=[{"value": "Warli people", "label": "<b>Warli</b> people", "kind": "path", "path": "A/Warli_people"},
                                       {"value": "warli ", "label": "containing 'warli'...", "kind": "pattern"}],
                                 content_type="application/json")
        if url == "https://web.archive.org/cdx/search/cdx":
            return fake_response(data=[["original"], ["https://health.example.ls/clinic-hours"], ["https://health.example.ls/clinics"],
                                       ["http://health.example.ls/clinic-hours/"]], content_type="application/json")
        if url == "https://archive.org/wayback/available":
            if params["url"] == "https://health.example.ls/clinic-hours" and archive_text is None:
                return fake_response(data={"archived_snapshots": {"closest": {
                    "available": True, "timestamp": "20251020120000", "url": "https://web.archive.org/web/20251020120000/" + params["url"]}}},
                    content_type="application/json")
            if archive_text is None:
                return fake_response(data={"archived_snapshots": {}}, content_type="application/json")
            return fake_response(data={"archived_snapshots": {"closest": {
                "available": True, "timestamp": "20251020120000", "url": "https://web.archive.org/web/20251020120000/" + params["url"]}}},
                content_type="application/json")
        if url.startswith("https://web.archive.org/web/20251020120000id_/"):
            return fake_response(text=archive_text or "<p>Clinic hours: every day.</p>")
        raise AssertionError("unexpected request: " + url)
    get.seen = seen
    return get


class ShelfTests(CheckerTests):
    """My shelf, Take my shelf with me (ZIP and Kiwix ZIM), and Then and now."""

    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})

    def edit_warli(self):
        FakeWikipedia.articles["Warli"].append(
            {"revid": 2, "user": "B", "comment": "more", "text": "The Warli are an Adivasi people of Maharashtra and Gujarat. They paint."})

    def upload(self, name, data, **form):
        import io
        return self.client.post("/shelf/file", data=dict(form, file=(io.BytesIO(data), name)),
                                content_type="multipart/form-data", follow_redirects=True)

    def test_pages_open_without_an_account(self):
        self.upload("gazette.txt", b"Notice 12.", title="Purple's gazette")
        self.client.post("/logout")
        for path in ["/shelf"]:
            page = self.client.get(path)
            self.assertEqual(page.status_code, 200, path)
            self.assertNotIn("Purple&#39;s gazette", page.data.decode())  # nobody else's things
        self.assertEqual(self.client.get("/shelf/1").status_code, 404)

    def test_keep_a_file_search_and_open_it(self):
        words = "Government Gazette of Lesotho. Notice 12: the village of Ha Mohale is renamed. " * 3
        page = self.upload("gazette.txt", words.encode(), title="Gazette 12", note="From the post office").data.decode()
        self.assertIn("On your shelf.", page)
        self.assertIn("Ha Mohale is renamed", page)
        self.assertIn("Read from the file (plain text)", page)
        listing = self.client.get("/shelf?q=mohale").data.decode()
        self.assertIn("Gazette 12", listing)
        self.assertIn("1 found", listing)
        self.assertIn("Nothing found.", self.client.get("/shelf?q=zebra").data.decode())
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        opened = self.client.get(f"/shelf/{item}/file")
        self.assertEqual((opened.status_code, opened.mimetype, opened.data), (200, "text/plain", words.encode()))
        self.assertIn("inline", opened.headers["Content-Disposition"])
        self.assertIn("attachment", self.client.get(f"/shelf/{item}/file?download=1").headers["Content-Disposition"])
        self.assertIn("1 kept", self.client.get("/").data.decode())  # on the home screen

    def test_web_pages_are_never_shown_inside_tripwire(self):
        """An uploaded HTML file could carry a script: it's only ever downloaded."""
        self.upload("page.html", b"<html><body><script>alert(1)</script>" + b"<p>Some words here.</p>" * 10 + b"</body></html>")
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        opened = self.client.get(f"/shelf/{item}/file")
        self.assertEqual(opened.mimetype, "application/octet-stream")
        self.assertIn("attachment", opened.headers["Content-Disposition"])
        self.assertEqual(opened.headers["X-Content-Type-Options"], "nosniff")

    def test_scans_read_on_the_device(self):
        page = self.upload("memo.pdf", make_pdf(["", ""]), read_text="Memo from the Ministry. " * 10, read_pages="2").data.decode()
        self.assertIn("Read on your device (scanned, 2 pages)", page)
        self.assertIn("Memo from the Ministry", page)
        # Too big to upload: only its words are kept.
        page = self.client.post("/shelf/file", data={"file_name": "huge-scan.pdf", "read_text": "Big scan words. " * 10,
                                                     "read_pages": "40"}, follow_redirects=True).data.decode()
        self.assertIn("keep your own copy of it", page)
        self.assertIn("Big scan words", page)
        self.assertNotIn("Open the original file", page)
        # A scan whose words weren't read yet can be read later.
        page = self.upload("photo.jpg", b"\xff\xd8\xff fake photo").data.decode()
        self.assertIn("Read the words on this device", page)
        item = self.tw.conn.execute("SELECT id FROM shelf_items WHERE file_name = 'photo.jpg'").fetchone()["id"]
        self.client.post(f"/shelf/{item}/text", json={"text": "Words in the photo.", "pages": 1})
        page = self.client.get(f"/shelf/{item}").data.decode()
        self.assertIn("Words in the photo.", page)
        self.assertIn("Read on your device (scanned, 1 page)", page)

    def test_keep_a_wikipedia_link_watch_it_and_compare_with_kiwix(self):
        kiwix_page = ("<html><body><h1>Warli</h1><p>The Warli are an Adivasi people of Maharashtra.</p>"
                      "<p>This article is issued from Wikipedia. The text is licensed under Creative Commons.</p></body></html>")
        self.edit_warli()
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=kiwix_page)):
            page = self.client.post("/shelf/link", data={"url": "https://en.wikipedia.org/wiki/Warli", "watch": "on",
                                                         "compare": "on"}, follow_redirects=True).data.decode()
        self.assertIn("They paint.", page)  # today's words are kept
        self.assertIn("Watching the original", page)
        self.assertIn("Kiwix&#39;s offline English Wikipedia (January 2024)", page)
        check = self.tw.conn.execute("SELECT * FROM before_checks").fetchone()
        self.assertEqual(check["before_date"], "2024-01-15")
        self.assertEqual(check["download_link"], "https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_maxi_2024-01.zim")
        self.assertNotIn("Creative Commons", check["details"])  # Kiwix's licence line isn't a change
        result = self.client.get(f"/before/{check['id']}").data.decode()
        self.assertIn("Gujarat", result)
        self.assertIn("On your shelf ✓", result)
        self.assertIn("Download it from Kiwix", result)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM watches WHERE target = 'Warli'").fetchone()["n"], 1)

    def test_keep_a_pdf_link(self):
        pdf = make_pdf(["Gazette notice: the road to Semonkong will be closed for repairs in November. " * 3])
        with mock.patch("tripwire.books.fetch_book", return_value=("notice.pdf", pdf)):
            page = self.client.post("/shelf/link", data={"url": "https://gov.example.ls/notice.pdf"},
                                    follow_redirects=True).data.decode()
        self.assertIn("Semonkong", page)
        self.assertIn("Open the original file", page)
        self.assertNotIn("Watching the original", page)
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        self.assertEqual(self.client.get(f"/shelf/{item}/file").data, pdf)

    def test_others_cannot_see_your_shelf(self):
        self.upload("mine.txt", b"Private words. " * 10)
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        self.client.post("/logout")
        self.tw.create_user("someone@example.org", "another horse")
        self.client.post("/login", data={"email": "someone@example.org", "password": "another horse"})
        for path in [f"/shelf/{item}", f"/shelf/{item}/file"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(f"/shelf/{item}/delete").status_code, 404)
        self.assertNotIn("Private words", self.client.get("/shelf?q=private").data.decode())
        import zipfile, io
        z = zipfile.ZipFile(io.BytesIO(self.client.get("/shelf/download.zip").data))
        self.assertNotIn("Private words", "".join(z.read(n).decode("utf-8", "ignore") for n in z.namelist()))

    def test_edit_and_remove(self):
        self.upload("a.txt", b"Words to keep. " * 10)
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        self.client.post(f"/shelf/{item}/edit", data={"title": "Better title", "note": "A note"})
        self.assertIn("Better title", self.client.get(f"/shelf/{item}").data.decode())
        self.client.post(f"/shelf/{item}/delete")
        self.assertEqual(self.client.get(f"/shelf/{item}").status_code, 404)

    def test_shelf_has_a_size_limit(self):
        from tripwire import shelf
        with mock.patch.object(shelf, "MAX_SHELF_BYTES", 500):
            page = self.upload("big.txt", b"Too many words. " * 50).data.decode()
        self.assertIn("Your shelf is full", page)

    def fill_shelf(self):
        self.upload("gazette.txt", "Ha Mohale is renamed. Lesotho gazette, “quoted” café. ".encode() * 5, title="Gazette 12")
        self.upload("scan.pdf", make_pdf(["", ""]), read_text="Words from a scan of Semonkong. " * 5, read_pages="2")

    def test_take_my_shelf_with_me_as_a_zip(self):
        import io, json, zipfile
        self.fill_shelf()
        response = self.client.get("/shelf/download.zip?name=Lesotho gazettes")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(".zip", response.headers["Content-Disposition"])
        z = zipfile.ZipFile(io.BytesIO(response.data))
        names = z.namelist()
        for expected in ["Lesotho-gazettes/index.html", "Lesotho-gazettes/shelf.json", "Lesotho-gazettes/README.txt",
                         "Lesotho-gazettes/items/0001-Gazette-12.html", "Lesotho-gazettes/text/0001-Gazette-12.txt",
                         "Lesotho-gazettes/files/0002-scan.pdf", "Lesotho-gazettes/static/fonts/vt323-latin-400-normal.woff2"]:
            self.assertIn(expected, names)
        index = z.read("Lesotho-gazettes/index.html").decode()
        self.assertIn("Lesotho gazettes", index)
        self.assertIn('href="items/0001-Gazette-12.html"', index)
        self.assertNotIn("purple@example.org", index)  # the copy may be shared: no email address in it
        item = z.read("Lesotho-gazettes/items/0001-Gazette-12.html").decode()
        self.assertIn("“quoted” café", item)
        self.assertIn('href="../static/fonts/vt323-latin-400-normal.woff2"'.replace('href="', 'url("').replace('woff2"', 'woff2")'), item)
        self.assertEqual(z.read("Lesotho-gazettes/files/0002-scan.pdf")[:5], b"%PDF-")
        listing = json.loads(z.read("Lesotho-gazettes/shelf.json"))
        self.assertEqual([i["title"] for i in listing["items"]], ["Gazette 12", "scan"])

    def test_take_my_shelf_with_me_for_kiwix(self):
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
        self.fill_shelf()
        response = self.client.get("/shelf/download.zim?name=Lesotho gazettes")
        self.assertEqual(response.status_code, 200)
        self.assertIn(".zim", response.headers["Content-Disposition"])
        path = os.path.join(self.tmp.name, "shelf.zim")
        with open(path, "wb") as f:
            f.write(response.data)
        zim = Archive(path)
        self.assertEqual(zim.main_entry.get_item().path, "index.html")
        self.assertEqual(zim.get_metadata("Title").decode(), "Lesotho gazettes")
        self.assertEqual(zim.get_metadata("Language").decode(), "eng")
        self.assertTrue(zim.has_illustration(48))
        self.assertIn("Ha Mohale is renamed", bytes(zim.get_entry_by_path("items/0001-Gazette-12.html").get_item().content).decode())
        self.assertEqual(bytes(zim.get_entry_by_path("files/0002-scan.pdf").get_item().content)[:5], b"%PDF-")
        search = Searcher(zim).search(Query().set_query("Semonkong"))
        found = list((getattr(search, "get_results", None) or search.getResults)(0, 10))  # libzim 4 / libzim 3
        self.assertEqual(found, ["items/0002-scan.html"])  # searchable inside Kiwix

    def test_empty_shelf_still_downloads(self):
        import io, zipfile
        self.assertIn("This shelf is empty", zipfile.ZipFile(io.BytesIO(self.client.get("/shelf/download.zip").data))
                      .read("My-shelf/index.html").decode())
        self.assertEqual(self.client.get("/shelf/download.zim").status_code, 200)

    def test_test_against_the_internet_archive(self):
        old = "<html><body><p>The clinic in Ha Mohale is open every day. Nurses: four.</p></body></html>"
        pages = {"https://health.example.ls/clinics": {"status": "ok", "text": "The clinic in Ha Mohale is open on Mondays. Nurses: one."}}
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(archive_text=old)), \
                mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            page = self.client.post("/before", data={"url": "https://health.example.ls/clinics", "source": "archive"},
                                    follow_redirects=True).data.decode()
        self.assertIn("the Internet Archive&#39;s copy (October 2025)", page)
        self.assertIn("every day", page)
        self.assertIn("Mondays", page)
        self.assertIn("Keep today's copy on my shelf", page)

    def test_internet_archive_offers_similar_saved_pages(self):
        pages = {"https://health.example.ls/clinics": {"status": "ok", "text": "Clinic hours: on Mondays."}}
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive()), \
                mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            page = self.client.post("/before", data={"url": "https://health.example.ls/clinics", "source": "archive"}).data.decode()
            self.assertIn("No exact copy in the Internet Archive", page)
            self.assertIn('value="https://health.example.ls/clinic-hours"', page)  # a similar saved page
            self.assertEqual(page.count('name="archive_url"'), 1)  # the same page twice (http, slash) is one choice
            self.assertNotIn('name="archive_url" value="https://health.example.ls/clinics"', page)  # not the page itself
            page = self.client.post("/before", data={"url": "https://health.example.ls/clinics", "source": "archive",
                                                     "archive_url": "https://health.example.ls/clinic-hours"},
                                    follow_redirects=True).data.decode()
        self.assertIn("the Internet Archive&#39;s copy of https://health.example.ls/clinic-hours (October 2025)", page)
        self.assertIn("every day", page)

    def test_test_against_kiwix_offers_similar_articles(self):
        kiwix_page = "<html><head><title>Warli</title></head><body><p>The Warli are an Adivasi people of Maharashtra.</p></body></html>"
        self.edit_warli()
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=None)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix"}).data.decode()
        self.assertIn("No exact copy in Kiwix", page)
        self.assertIn("has no article called “Warli”", page)
        self.assertIn("Warli people", page)
        self.assertIn('value="A/Warli_people"', page)
        self.assertIn("Try the Internet Archive instead", page)
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=kiwix_page)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix",
                                                     "kiwix_path": "A/Warli_people"}, follow_redirects=True).data.decode()
        self.assertIn("“Warli people” in Kiwix&#39;s offline English Wikipedia (January 2024)", page)

    def test_kiwix_is_read_from_its_raw_address(self):
        """Kiwix's "raw" address sends the page exactly as stored in the offline copy."""
        page = "<html><head><title>Warli</title></head><body><h1>Warli</h1><p>The Warli are an Adivasi people of Maharashtra.</p></body></html>"
        self.edit_warli()
        fake = fake_kiwix_and_archive(kiwix_article=page)
        with mock.patch.object(sources, "_get", fake):
            result = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix"},
                                      follow_redirects=True).data.decode()
        self.assertIn("https://library.kiwix.org/raw/wikipedia_en_all_maxi_2024-01/content/A/Warli", fake.seen)
        self.assertIn("What was compared", result)
        self.assertIn("The Warli are an Adivasi people of Maharashtra.", result)  # the older copy's own words
        self.assertIn("The page's own title: “warli", result)
        self.assertIn("Wikipedia&#39;s version 2", result)  # today's exact version, which anyone can open

    def test_robot_check_pages_are_never_compared(self):
        """Basotho was once compared with ~59 words of a "prove you're not a bot" page. Never again."""
        robot_check = ("<html><head><title>Making sure you&#39;re not a bot!</title></head><body><h1>Making sure you're not a bot!</h1>"
                       "<p>You are seeing this because the administrator has set up Anubis to protect the server against AI companies "
                       "aggressively scraping websites. Please enable JavaScript.</p></body></html>")
        self.edit_warli()
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_raw=robot_check)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix"}).data.decode()
        self.assertIn("robot check", page)
        self.assertNotIn("What was compared", page)
        self.assertIn("Compare with Wikipedia's version from 2024-01-15", page)
        self.assertIn("Kiwix's site didn't let Tripwire in", page)  # an honest way round it
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM before_checks").fetchone()["n"], 0)

    def test_a_page_with_the_wrong_title_is_not_the_article(self):
        other = "<html><head><title>Main Page</title></head><body><p>Welcome to Wikipedia, the free encyclopedia.</p></body></html>"
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_raw=other)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix"}).data.decode()
        self.assertIn("has no article called “Warli”", page)

    def test_a_suspiciously_short_older_copy_is_refused(self):
        FakeWikipedia.articles["Warli"].append({"revid": 3, "user": "C", "comment": "long", "text": "The Warli paint. " * 400})
        tiny = "<html><head><title>Warli</title></head><body><p>Warli. Loading.</p></body></html>"
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=tiny)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "kiwix"},
                                    follow_redirects=True).data.decode()
        self.assertIn("almost certainly not the real page", page)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM before_checks").fetchone()["n"], 0)

    def test_compare_with_wikipedias_own_version_from_a_date(self):
        self.edit_warli()
        page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "wikipedia_date",
                                                 "on_date": "2026-10-15"}, follow_redirects=True).data.decode()
        self.assertIn("Wikipedia&#39;s own saved version from 2026-10-01 (as it was on 2026-10-15, the date of Kiwix&#39;s copy)", page)
        self.assertIn("https://en.wikipedia.org/w/index.php?oldid=1", page)  # the exact old version, on Wikipedia itself
        self.assertIn("Gujarat", page)

    def test_kiwix_is_only_for_wikipedia(self):
        pages = {"https://news.example.org/": {"status": "ok", "text": "Today's news."}}
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive()), \
                mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            page = self.client.post("/before", data={"url": "https://news.example.org/", "source": "kiwix"},
                                    follow_redirects=True).data.decode()
        self.assertIn("Kiwix keeps offline copies of whole collections", page)

    def test_without_a_choice_kiwix_then_the_internet_archive(self):
        """A Wikipedia article Kiwix doesn't have (e.g. too new) uses the Internet Archive instead."""
        old = "<html><body><p>The Warli are an Adivasi people.</p></body></html>"
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=None, archive_text=old)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli"}, follow_redirects=True).data.decode()
        self.assertIn("the Internet Archive&#39;s copy", page)

    def test_test_buttons_on_watched_items(self):
        self.tw.add_watch(self.theme, "Warli")
        page = self.client.get("/watching").data.decode()
        self.assertIn("Test against Kiwix", page)
        self.assertIn("Test against Internet Archive", page)
        self.assertIn('value="https://en.wikipedia.org/wiki/Warli"', page)
        old = "<html><body><p>The Warli are an Adivasi people.</p></body></html>"
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(archive_text=old)):
            self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli", "source": "archive"})
        self.assertNotIn("Last Internet Archive test", self.client.get("/watching").data.decode())  # results don't pile up there

    def stamp_of(self, item_id):
        return self.client.get(f"/shelf/{item_id}/stamp.json")

    def test_uploaded_copies_are_stamped_honestly(self):
        import io, json
        words = b"Government Gazette, Notice 12: the road to Semonkong will close. " * 3
        page = self.upload("gazette.txt", words).data.decode()
        self.assertIn("Stamp of originality", page)
        self.assertIn("Uploaded by its owner", page)  # Tripwire didn't fetch it, and says so
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        stamp_file = self.stamp_of(item)
        self.assertIn("attachment", stamp_file.headers["Content-Disposition"])
        stamp = json.loads(stamp_file.data)
        self.assertEqual((stamp["kind"], stamp["how"], stamp["file_name"]), ("Tripwire copy stamp", "uploaded", "gazette.txt"))
        from tripwire import stamp as stamps
        self.assertEqual(stamp["file_sha256"], stamps.fingerprint(words))
        # Anyone can check it, no account needed.
        self.client.post("/logout")
        result = self.client.post("/verify", data={"stamp": stamp_file.data.decode(), "copy": (io.BytesIO(words), "gazette.txt")},
                                  content_type="multipart/form-data").data.decode()
        self.assertIn("✓ Genuine stamp", result)
        self.assertIn("is exactly the stamped file", result)
        result = self.client.post("/verify", data={"stamp": stamp_file.data.decode(), "copy": (io.BytesIO(words + b"!"), "edited.txt")},
                                  content_type="multipart/form-data").data.decode()
        self.assertIn("is not the stamped copy", result)  # one extra character, and it no longer matches

    def test_a_changed_stamp_is_caught(self):
        import json
        self.upload("memo.txt", b"Memo from the Ministry of Local Government. " * 3)
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        stamp = json.loads(self.stamp_of(item).data)
        for field, fake in [("taken_at", "2020-01-01T00:00:00Z"), ("source_url", "https://gov.example.ls/real.pdf"), ("how", "fetched")]:
            forged = dict(stamp, **{field: fake})
            result = self.client.post("/verify", data={"stamp": json.dumps(forged)}).data.decode()
            self.assertIn("✗ Not a genuine stamp", result, field)
        self.assertIn("isn&#39;t a stamp", self.client.post("/verify", data={"stamp": "hello"}).data.decode())
        from tripwire import stamp as stamps
        with mock.patch.dict(os.environ, {"TRIPWIRE_STAMP_SECRET": "another site"}):
            other = stamps.Stamper(self.tw.conn).make(title="x", how="uploaded", taken_at="2026-10-30T00:00:00Z")
        self.assertIn("different Tripwire site", self.client.post("/verify", data={"stamp": json.dumps(other)}).data.decode())

    def test_copies_fetched_by_tripwire_say_where_from(self):
        import json
        self.edit_warli()
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive()):
            page = self.client.post("/shelf/link", data={"url": "https://en.wikipedia.org/wiki/Warli"}, follow_redirects=True).data.decode()
        self.assertIn("Fetched by Tripwire itself", page)
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        stamp = json.loads(self.stamp_of(item).data)
        self.assertEqual(stamp["how"], "fetched")
        self.assertEqual(stamp["source_url"], "https://en.wikipedia.org/wiki/Warli")
        self.assertEqual(stamp["wikipedia_version"], "https://en.wikipedia.org/w/index.php?oldid=2")  # anyone can open it

    def test_words_read_later_are_added_to_the_stamp(self):
        import json
        self.upload("photo.jpg", b"\xff\xd8\xff fake photo")
        item = self.tw.conn.execute("SELECT id FROM shelf_items").fetchone()["id"]
        before = json.loads(self.stamp_of(item).data)
        self.assertIsNone(before["text_sha256"])
        self.client.post(f"/shelf/{item}/text", json={"text": "Words in the photo.", "pages": 1})
        after = json.loads(self.stamp_of(item).data)
        self.assertEqual(after["taken_at"], before["taken_at"])  # still the moment it was kept
        self.assertEqual(after["file_sha256"], before["file_sha256"])
        self.assertIn("text_added_at", after)
        self.assertIn("✓ Genuine stamp", self.client.post("/verify", data={"stamp": json.dumps(after)}).data.decode())

    def test_stamps_travel_with_the_download(self):
        import io, json, zipfile
        self.upload("gazette.txt", b"Ha Mohale is renamed. " * 10, title="Gazette 12")
        z = zipfile.ZipFile(io.BytesIO(self.client.get("/shelf/download.zip").data))
        stamp_path = "My-shelf/stamps/0001-Gazette-12.stamp.json"
        self.assertIn(stamp_path, z.namelist())
        self.assertIn("Stamp of originality", z.read("My-shelf/items/0001-Gazette-12.html").decode())
        self.assertIn("/verify", z.read("My-shelf/items/0001-Gazette-12.html").decode())
        # The words in the download can be checked against the stamp.
        result = self.client.post("/verify", data={"stamp": z.read(stamp_path).decode(),
                                                   "copy": (io.BytesIO(z.read("My-shelf/text/0001-Gazette-12.txt")), "words.txt")},
                                  content_type="multipart/form-data").data.decode()
        self.assertIn("holds exactly the stamped words", result)

    def test_kiwix_catalogue(self):
        from tripwire import before
        copies = before.parse_catalog(KIWIX_CATALOG.encode())
        self.assertEqual([c["flavour"] for c in copies], ["mini", "maxi", "maxi"])
        self.assertEqual(copies[1]["content_url"], "https://library.kiwix.org/content/wikipedia_en_all_maxi_2024-01")
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive()):
            newest = before.kiwix_copy("en")
            self.assertEqual(newest["date"], "2024-01-15")  # the newest full copy, not the newer "mini" one
            self.assertIsNone(before.kiwix_copy("st"))
        # Older copies put the kind in the name, with no separate <flavour>.
        old_style = KIWIX_CATALOG.replace("<flavour>maxi</flavour>", "").replace("<flavour>mini</flavour>", "")
        self.assertEqual([c["flavour"] for c in before.parse_catalog(old_style.encode())], ["mini", "maxi", "maxi"])


class BookLinkTests(CheckerTests):
    """Watching the links a book came from."""

    def test_watch_a_book_at_a_link(self):
        """From Books & videos, the links a book came from can be watched (EPUB, plain text or a web page)."""
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        client = webapp.app.test_client()
        client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        responses = {"https://books.example.org/old.txt": mock.Mock(status_code=200, headers={"Content-Type": "text/plain; charset=utf-8"},
                                                                    content=OLD_BOOK.encode(), text=OLD_BOOK, url="https://books.example.org/old.txt"),
                     "https://books.example.org/new.txt": mock.Mock(status_code=200, headers={"Content-Type": "text/plain"},
                                                                    content=NEW_BOOK.encode(), text=NEW_BOOK, url="https://books.example.org/new.txt")}
        with mock.patch.object(sources, "_get", lambda url, **kw: responses[url]), \
                mock.patch("tripwire.books.fetch_book", side_effect=lambda url: (url.rsplit("/", 1)[1], responses[url].content)):
            page = client.post("/books", data={"title": "A book", "link_a": "https://books.example.org/old.txt",
                                               "link_b": "https://books.example.org/new.txt", "watch_links": "on"},
                               follow_redirects=True).data.decode()
        self.assertIn("Watching https://books.example.org/old.txt too", page)
        watched = self.tw.conn.execute("SELECT target, baseline_text FROM watches WHERE kind = 'web' ORDER BY id").fetchall()
        self.assertEqual([w["target"] for w in watched], ["https://books.example.org/old.txt", "https://books.example.org/new.txt"])
        self.assertIn(OLD_BOOK.split()[0], watched[0]["baseline_text"])

    def test_an_epub_at_a_link_is_read_as_a_book(self):
        epub = make_epub([OLD_BOOK])
        response = mock.Mock(status_code=200, headers={"Content-Type": "application/epub+zip"}, content=epub, url="https://b.example.org/x.epub")
        with mock.patch.object(sources, "_get", return_value=response):
            self.assertIn("Augustus Gloop was enormously fat", sources.fetch_page("https://b.example.org/x.epub")["text"])


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
        webapp.app.config.update(DB_URL=None, TESTING=False, PROPAGATE_EXCEPTIONS=False)
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


class VaultTests(CheckerTests):
    """Locked shelf items: Tripwire stores them but can't read them. (The locking itself runs in
    the browser and is checked in a real browser too; these check everything on the server.)"""

    RECORD = {"v": 1, "kdf": "PBKDF2-SHA256", "iterations": 600000, "salt": "c2FsdHNhbHRzYWx0c2FsdA==",
              "recovery_salt": "cmVjb3ZlcnlyZWNvdmVyeQ==", "key": "bG9ja2VkLWtleS1ieS1wYXNzcGhyYXNlLWJsYWJsYQ==",
              "recovery_key": "bG9ja2VkLWtleS1ieS1yZWNvdmVyeS1jb2RlLWJsYWJs"}

    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})

    def make_vault(self):
        self.assertEqual(self.client.post("/vault/setup", json=self.RECORD).json, {"ok": True})

    def lock(self, title="U2VjcmV0IHRpdGxlIGxvY2tlZA==", sealed="TG9ja2VkIGRldGFpbHMgaGVyZQ==", file=b"\x00locked bytes"):
        import io
        data = {"sealed_title": title, "sealed": sealed}
        if file is not None:
            data["file"] = (io.BytesIO(file), "locked.bin")
        return self.client.post("/shelf/locked", data=data, content_type="multipart/form-data")

    def test_needs_an_account_and_a_vault(self):
        self.assertEqual(self.lock().status_code, 400)  # no vault yet
        self.assertIn("Make your vault", self.client.get("/vault").data.decode())
        self.client.post("/logout")
        self.assertTrue(self.client.get("/vault").headers["Location"].endswith("/signup"))
        self.assertTrue(self.client.get("/vault/key").headers["Location"].endswith("/signup"))

    def test_vault_is_stored_only_locked_and_only_once(self):
        self.make_vault()
        self.assertEqual(self.client.get("/vault/key").json, self.RECORD)
        self.assertEqual(self.client.post("/vault/setup", json=self.RECORD).status_code, 409)  # can't be replaced
        for broken in [dict(self.RECORD, iterations=1000), dict(self.RECORD, key="not base64!"),
                       dict(self.RECORD, kdf="none"), {"v": 1}]:
            self.client.post("/vault/reset", data={"password": "correct horse", "sure": "yes"})
            self.assertEqual(self.client.post("/vault/setup", json=broken).status_code, 400)

    def test_locked_items_are_stored_as_they_arrive(self):
        self.make_vault()
        url = self.lock().json["url"]
        row = self.tw.conn.execute("SELECT * FROM shelf_items").fetchone()
        self.assertEqual((row["locked"], row["title"], row["note"], row["text"], row["source_url"]),
                         (1, "Locked item", "", "", None))
        self.assertEqual(bytes(row["file_data"]), b"\x00locked bytes")
        sealed = self.client.get(url + "/sealed").json
        self.assertEqual(sealed["sealed_title"], "U2VjcmV0IHRpdGxlIGxvY2tlZA==")
        self.assertTrue(sealed["has_file"])
        page = self.client.get(url).data.decode()
        self.assertIn("Tripwire can't read it", page)
        self.assertIn('data-sealed-title="U2VjcmV0IHRpdGxlIGxvY2tlZA=="', self.client.get("/shelf").data.decode())
        file = self.client.get(url + "/file")
        self.assertEqual(file.data, b"\x00locked bytes")
        self.assertIn("attachment", file.headers["Content-Disposition"])  # never shown as a page
        # Not readable, so not searchable, editable or readable here.
        self.assertNotIn("Locked item", self.client.get("/shelf?q=locked").data.decode().split("On your shelf")[1].split("Using")[0])
        self.client.post(url + "/edit", data={"title": "Changed", "note": "x"})
        self.assertEqual(self.tw.conn.execute("SELECT title FROM shelf_items").fetchone()["title"], "Locked item")

    def test_only_locked_looking_data_is_accepted(self):
        self.make_vault()
        for title, sealed in [("<script>alert(1)</script>", "TG9ja2Vk"), ("TG9ja2Vk", "plain words, not locked"), ("", "TG9ja2Vk")]:
            self.assertEqual(self.lock(title=title, sealed=sealed).status_code, 400)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM shelf_items").fetchone()["n"], 0)

    def test_someone_else_cant_get_them(self):
        self.make_vault()
        url = self.lock().json["url"]
        self.tw.create_user("other@example.org", "other password")
        other = __import__("app").app.test_client()
        other.post("/login", data={"email": "other@example.org", "password": "other password"})
        for path in (url, url + "/sealed", url + "/file"):
            self.assertEqual(other.get(path).status_code, 404, path)
        self.assertEqual(other.get("/vault/key").status_code, 404)

    def test_take_it_with_me_still_locked(self):
        import io, json, zipfile
        self.make_vault()
        self.lock()
        self.lock(title="QW5vdGhlciB0aXRsZQ==", file=None)
        response = self.client.get("/shelf/download.zip?name=Mine")
        z = zipfile.ZipFile(io.BytesIO(b"".join(response.response)))
        names = z.namelist()
        self.assertIn("Mine/locked/unlock.html", names)
        data = json.loads(z.read("Mine/locked/locked.json"))
        self.assertEqual(data["vault"], self.RECORD)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["file"], "AGxvY2tlZCBieXRlcw==")
        page = z.read("Mine/locked/unlock.html").decode()
        self.assertIn("TripwireVault", page)  # the unlocking code travels with it
        self.assertNotIn("</script>\n<script type=\"application/json\" id=\"data\"></script>", page)
        self.assertNotIn("Locked item", z.read("Mine/index.html").decode())  # not among the readable pages

    def test_forgot_everything_start_again(self):
        self.make_vault()
        self.lock()
        self.client.post("/vault/reset", data={"password": "wrong", "sure": "yes"})
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM shelf_items").fetchone()["n"], 1)
        self.client.post("/vault/reset", data={"password": "correct horse", "sure": "yes"})
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM shelf_items").fetchone()["n"], 0)
        self.assertIsNone(self.tw.user(self.user)["vault"])

    def test_new_passphrase_keeps_the_recovery_copy(self):
        self.make_vault()
        changed = dict(self.RECORD, salt="bmV3c2FsdG5ld3NhbHRuZXc=", key="bmV3bHktbG9ja2VkLWtleS1ieS1uZXctcGFzcw==")
        self.assertEqual(self.client.post("/vault/passphrase", json=changed).json, {"ok": True})
        self.assertEqual(self.client.get("/vault/key").json["key"], changed["key"])
        swapped = dict(changed, recovery_key="c29tZW9uZS1lbHNlcy1yZWNvdmVyeS1rZXk=")
        self.assertEqual(self.client.post("/vault/passphrase", json=swapped).status_code, 400)

    def test_core_is_served(self):
        response = self.client.get("/vault-core.js")
        self.assertIn(b"TripwireVault", response.data)
        self.assertIn("javascript", response.headers["Content-Type"])


class PrivacyTests(CheckerTests):
    """Usernames instead of emails; anonymous, locked feedback; locked "Test against" results."""

    SEALED = "TG9ja2VkIGluIHRoZSBicm93c2VyLCBub3QgcmVhZGFibGUgaGVyZQ=="

    def setUp(self):
        super().setUp()
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        self.client = webapp.app.test_client()
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})

    def test_everyone_gets_a_random_old_internet_name(self):
        from tripwire.checker import new_handle
        names = {new_handle() for _ in range(200)}
        self.assertGreater(len(names), 150)
        self.assertTrue(all("@" not in n and n.isascii() for n in names))
        other = self.tw.create_user("someone@example.org", "a long password")
        self.assertNotEqual(self.tw.user(other)["handle"], self.tw.user(self.user)["handle"])
        # An account made before names existed gets one on its next visit.
        self.tw.conn.execute("UPDATE users SET handle = NULL WHERE id = ?", (self.user,))
        page = self.client.get("/account").data.decode()
        self.assertTrue(self.tw.user(self.user)["handle"])
        self.assertIn(self.tw.user(self.user)["handle"], page)

    def test_feedback_is_anonymous_unless_signed(self):
        handle = self.tw.user(self.user)["handle"]
        self.client.post("/feedback", data={"tried": "Books", "message": "Unsigned thoughts"})
        self.client.post("/feedback", data={"tried": "Stats", "message": "Signed thoughts", "sign": "yes"})
        rows = self.tw.conn.execute("SELECT user_id, signed_as, message FROM feedback ORDER BY id").fetchall()
        self.assertEqual([(r["user_id"], r["signed_as"]) for r in rows], [(None, None), (None, handle)])
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "purple@example.org"}):
            page = self.client.get("/feedback").data.decode()
        self.assertIn("Anonymous", page)
        self.assertIn("Signed: " + handle, page)
        self.assertNotIn("purple@example.org", page.split("<main")[1])
        # A visitor without an account can't sign (there's no name to sign with).
        self.client.post("/logout")
        self.client.post("/feedback", data={"message": "Visitor thoughts", "sign": "yes"})
        self.assertIsNone(self.tw.conn.execute("SELECT signed_as FROM feedback ORDER BY id DESC").fetchone()["signed_as"])

    def lock_the_box(self):
        self.tw.conn.execute("UPDATE users SET vault = '{}' WHERE id = ?", (self.user,))
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "purple@example.org"}):
            return self.client.post("/feedback/key", json={"public": "QmFzZTY0UHVibGljS2V5", "private": "TG9ja2VkUHJpdmF0ZUtleQ=="})

    def test_locked_feedback_box_takes_only_locked_messages(self):
        self.client.post("/feedback", data={"message": "Written before the lock"})
        self.assertEqual(self.client.post("/feedback/key", json={"public": "x", "private": "y"}).status_code, 404)  # not the owner
        self.assertEqual(self.lock_the_box().json, {"ok": True})
        self.assertEqual(self.lock_the_box().status_code, 409)  # can't be swapped for another key
        self.assertIn('data-lock-to="QmFzZTY0UHVibGljS2V5"', self.client.get("/about").data.decode())
        # Readable feedback is refused now: it has to be locked in the browser.
        page = self.client.post("/feedback", data={"message": "Readable words"}, follow_redirects=True).data.decode()
        self.assertIn("needs JavaScript", page)
        self.assertEqual(self.tw.totals()["feedback"], 1)
        self.client.post("/feedback", data={"sealed": self.SEALED})
        row = self.tw.conn.execute("SELECT * FROM feedback ORDER BY id DESC").fetchone()
        self.assertEqual((row["message"], row["tried"], row["sealed"], row["user_id"]), ("", "", self.SEALED, None))
        # The older readable message gets locked by the owner's browser.
        first = self.tw.conn.execute("SELECT id FROM feedback ORDER BY id").fetchone()["id"]
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "purple@example.org"}):
            self.assertEqual(self.client.post(f"/feedback/{first}/lock", json={"sealed": self.SEALED}).json, {"ok": True})
            page = self.client.get("/feedback").data.decode()
        self.assertNotIn("Written before the lock", page)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM feedback WHERE message != ''").fetchone()["n"], 0)

    def test_starting_the_feedback_lock_again(self):
        self.lock_the_box()
        self.client.post("/feedback", data={"sealed": self.SEALED})
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "purple@example.org"}):
            self.client.post("/feedback/key/reset", data={"password": "wrong", "sure": "yes"})
            self.assertEqual(self.tw.totals()["feedback"], 1)
            self.client.post("/feedback/key/reset", data={"password": "correct horse", "sure": "yes"})
        self.assertEqual(self.tw.totals()["feedback"], 0)
        self.assertIsNone(self.tw.setting("feedback_key"))

    def make_check(self, user_id=None):
        from tripwire import before
        return before.save(self.tw.conn, user_id or self.user, {
            "url": "https://en.wikipedia.org/wiki/Warli", "title": "Warli", "source": "kiwix",
            "source_name": "Kiwix's offline Wikipedia (January 2024)", "before_date": "2024-01-15",
            "before_link": "https://library.kiwix.org/x", "download_link": None, "category": "edit",
            "summary": "12 words changed", "details": {"words_before": 100, "words_after": 104, "hunk_count": 1,
                                                        "hunks": [{"removed": "old", "added": "new", "before": "", "after": ""}]}})

    def test_test_results_get_locked_and_forgotten(self):
        check = self.make_check()
        self.assertEqual(self.client.get(f"/before/{check}/record").status_code, 404)  # no vault: nothing to lock with
        self.assertIn("Make a vault", self.client.get(f"/before/{check}").data.decode())
        self.tw.conn.execute("UPDATE users SET vault = '{}' WHERE id = ?", (self.user,))
        self.assertIn(f'data-lock-checks="{check}"', self.client.get(f"/before/{check}").data.decode())
        self.assertIn(f'data-lock-checks="{check}"', self.client.get("/watching").data.decode())
        record = self.client.get(f"/before/{check}/record").json
        self.assertEqual((record["url"], record["category_name"]), ("https://en.wikipedia.org/wiki/Warli", "Edit"))
        self.assertEqual(self.client.post(f"/before/{check}/lock", json={"sealed_title": "plain words", "sealed": "x"}).status_code, 400)
        self.assertEqual(self.client.post(f"/before/{check}/lock", json={"sealed_title": self.SEALED, "sealed": self.SEALED}).json, {"ok": True})
        row = self.tw.conn.execute("SELECT * FROM before_checks").fetchone()
        for column in ("url", "source", "source_name", "summary", "category"):
            self.assertEqual(row[column], "", column)
        self.assertEqual((row["title"], row["details"], row["before_link"]), ("Locked comparison", "{}", None))
        page = self.client.get(f"/before/{check}").data.decode()
        self.assertIn("Tripwire doesn't know which page this was", page)
        self.assertNotIn("Warli", page)
        self.assertEqual(self.client.get(f"/before/{check}/sealed").json["sealed"], self.SEALED)
        self.assertEqual(self.client.post(f"/before/{check}/shelf").status_code, 404)  # Tripwire no longer knows the page

    def test_nobody_else_can_lock_or_read_my_results(self):
        check = self.make_check()
        self.tw.conn.execute("UPDATE users SET vault = '{}'")
        self.tw.create_user("other@example.org", "other password")
        other = __import__("app").app.test_client()
        other.post("/login", data={"email": "other@example.org", "password": "other password"})
        self.assertEqual(other.get(f"/before/{check}/record").status_code, 404)
        self.assertEqual(other.post(f"/before/{check}/lock", json={"sealed_title": self.SEALED, "sealed": self.SEALED}).status_code, 400)
        self.assertEqual(self.tw.conn.execute("SELECT locked FROM before_checks").fetchone()["locked"], 0)

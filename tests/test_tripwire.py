"""Tests for Tripwire. Run with:   python -m unittest -v

These never touch the internet: Wikipedia and web pages are replaced by small
pretend versions whose content the tests control."""

import os
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
                conn.execute("DROP TABLE IF EXISTS shelf_items, before_checks, feedback, stats_cache, book_jobs, book_comparisons, archive_copies, entries, watches, themes, users CASCADE")
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
        for path in ["/watching", "/changes", "/archive", "/summary", "/videos", "/books", "/stats", "/live", "/about"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)
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
        self.assertIn(b"new@example.org", response.data)
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
        self.assertIn("No account needed", page)
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
        self.assertIn(b"1 change across 1", self.client.get("/summary").data)
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
        archive_page = self.client.get("/archive").data
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

    def test_videos_dont_show_on_watching_page(self):
        self.add(player=youtube_player())
        import app as webapp
        webapp.app.config.update(DB_URL=self.db_path, TESTING=True)
        client = webapp.app.test_client()
        client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        self.assertNotIn("Warli painting", client.get("/watching").data.decode())
        page = client.get("/videos").data.decode()
        self.assertIn("Warli painting: a living tradition", page)
        self.assertIn("Available", page)
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Second video"), title="Second video")):
            response = client.post("/single-links", data={"target": "https://youtu.be/aaaaaaaaaaa"}, follow_redirects=True)
        self.assertIn("Now watching that video", response.data.decode())  # pasted in the wrong place, still works
        self.assertIn("Second video", client.get("/videos").data.decode())


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
        self.assertIn('href="/stats" class="on" aria-current="page"', page)
        self.assertIn('href="/watching"', page)
        self.assertIn("<summary >More</summary>", page.replace('<summary class="on">', "<summary >"))
        self.assertIn('href="/summary" >Summary</a>', page)


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
            self.assertIn("Email isn't set up on this site yet", self.client.get("/summary").data.decode())
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
            self.assertIn('value="weekly" checked', self.client.get("/summary").data.decode())  # the starting choice
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (1, 0))
            self.client.post("/summary/email", data={"every": "off"})
            self.assertIn('value="off" checked', self.client.get("/summary").data.decode())
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (0, 0))
            self.client.post("/summary/email", data={"every": "daily"})
            page = self.client.get("/summary").data.decode()
            self.assertIn("Daily summary", page)
            self.assertEqual((due_on(monday_1st), due_on(tuesday_2nd)), (1, 1))
            self.client.post("/summary/email", data={"every": "monthly"})
            self.assertIn("Monthly summary", self.client.get("/summary").data.decode())
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
        self.assertIn("[9] ABOUT", self.client.get("/").data.decode())

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
        self.assertIn("/books/jobs/", response.headers["Location"])  # straight to the Frankenstein comparison
        totals = self.tw.totals()
        self.assertEqual(totals["watched"], 11)  # 10 articles + 1 video
        self.assertEqual(totals["changes"], 10)
        job = self.tw.conn.execute("SELECT title, link_a, link_b FROM book_jobs").fetchone()
        self.assertIn("Frankenstein", job["title"])
        self.assertIn("41445", job["link_a"])
        # Pressing it again adds nothing new.
        with mock.patch.object(sources, "_get", fake_video_site(player=youtube_player(title="Me at the zoo"))):
            self.client.post("/demo-data")
        self.assertEqual(self.tw.totals()["watched"], 11)
        self.assertEqual(self.tw.conn.execute("SELECT COUNT(*) AS n FROM book_jobs").fetchone()["n"], 1)
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

    def test_live_page(self):
        page = self.client.get("/live").data.decode()
        self.assertIn("Live watch", page)
        self.assertIn('href="/live"', page)  # in the menu
        self.assertEqual(self.client.post("/live/check", json={"url": "https://youtu.be/aaaaaaaaaaa"}).status_code, 400)
        self.client.post("/logout")
        self.assertEqual(self.client.get("/live").status_code, 200)  # no account needed


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

    def test_switch_to_hindi_and_sesotho(self):
        response = self.client.get("/language/hi?next=/about")
        self.assertTrue(response.headers["Location"].endswith("/about"))
        page = self.client.get("/about").data.decode()
        self.assertIn('<html lang="hi">', page)
        self.assertIn("Hindi: still being built", page)  # the pop-up, once
        self.assertIn("send a pull request", page)
        self.assertIn("github.com/MissPurrple/tripwire", page)
        self.assertIn("यह हिंदी संस्करण अभी बन रहा है", page)  # the banner
        again = self.client.get("/about").data.decode()
        self.assertNotIn("Hindi: still being built", again)  # pop-up only right after switching
        self.assertIn("यह हिंदी संस्करण अभी बन रहा है", again)  # banner stays
        self.client.get("/language/st")
        page = self.client.get("/").data.decode()
        self.assertIn("[L] KENA", page)
        self.assertIn('<html lang="st">', page)
        self.client.get("/language/en")
        page = self.client.get("/").data.decode()
        self.assertIn("[L] LOG IN", page)
        self.assertNotIn("language-banner", page)

    def test_language_switch_is_safe(self):
        self.assertEqual(self.client.get("/language/xx").status_code, 404)
        self.assertTrue(self.client.get("/language/hi?next=//evil.example").headers["Location"].endswith("/"))

    def test_translations_menus_after_login(self):
        self.client.post("/login", data={"email": "purple@example.org", "password": "correct horse"})
        self.client.get("/language/hi")
        page = self.client.get("/watching").data.decode()
        for word in ["निगरानी", "किताबें", "आँकड़े", "लॉग आउट"]:
            self.assertIn(word, page)

    def test_every_translation_file_is_valid(self):
        from tripwire import i18n
        english_keys = {"Watching", "Books", "language_notice"}
        for code, data in i18n.LANGUAGES.items():
            if code != "en":
                self.assertTrue(english_keys <= set(data), code)
                self.assertIn("Draft", data["_status"])


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
                     headers={"Content-Type": content_type}, url=url, json=lambda: data or {})


def fake_kiwix_and_archive(kiwix_article=None, archive_text=None):
    """A pretend library.kiwix.org and Internet Archive."""
    def get(url, params=None, **kwargs):
        if url.endswith("/catalog/v2/entries"):
            name = params["name"]
            return fake_response(text=KIWIX_CATALOG if name == "wikipedia_en_all" else "<feed xmlns='http://www.w3.org/2005/Atom'/>",
                                 content_type="application/atom+xml")
        if "/content/wikipedia_en_all_maxi_2024-01/" in url:
            if kiwix_article and url.endswith("/A/Warli"):
                return fake_response(text=kiwix_article, url=url)
            return fake_response(status=404)
        if url == "https://archive.org/wayback/available":
            if archive_text is None:
                return fake_response(data={"archived_snapshots": {}}, content_type="application/json")
            return fake_response(data={"archived_snapshots": {"closest": {
                "available": True, "timestamp": "20251020120000", "url": "https://web.archive.org/web/20251020120000/" + params["url"]}}},
                content_type="application/json")
        if url.startswith("https://web.archive.org/web/20251020120000id_/"):
            return fake_response(text=archive_text)
        raise AssertionError("unexpected request: " + url)
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
        for path in ["/shelf", "/before"]:
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

    def test_then_and_now_for_a_web_page(self):
        old = "<html><body><p>The clinic in Ha Mohale is open every day. Nurses: four.</p></body></html>"
        pages = {"https://health.example.ls/clinics": {"status": "ok", "text": "The clinic in Ha Mohale is open on Mondays. Nurses: one."}}
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(archive_text=old)), \
                mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            page = self.client.post("/before", data={"url": "https://health.example.ls/clinics"}, follow_redirects=True).data.decode()
        self.assertIn("the Internet Archive&#39;s copy (October 2025)", page)
        self.assertIn("every day", page)
        self.assertIn("Mondays", page)
        self.assertIn("Keep today's copy on my shelf", page)
        self.assertIn("health.example.ls/clinics", self.client.get("/before").data.decode())  # listed

    def test_then_and_now_falls_back_to_the_internet_archive(self):
        """A Wikipedia article Kiwix doesn't have (e.g. too new) uses the Internet Archive instead."""
        old = "<html><body><p>The Warli are an Adivasi people.</p></body></html>"
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive(kiwix_article=None, archive_text=old)):
            page = self.client.post("/before", data={"url": "https://en.wikipedia.org/wiki/Warli"}, follow_redirects=True).data.decode()
        self.assertIn("the Internet Archive&#39;s copy", page)

    def test_then_and_now_with_no_older_copy(self):
        pages = {"https://new.example.org/": {"status": "ok", "text": "Brand new page."}}
        with mock.patch.object(sources, "_get", fake_kiwix_and_archive()), \
                mock.patch.object(sources, "fetch_page", lambda url: pages[url]):
            page = self.client.post("/before", data={"url": "https://new.example.org/"}, follow_redirects=True).data.decode()
        self.assertIn("couldn&#39;t find an older copy", page)

    def test_then_and_now_button_on_watched_items(self):
        self.tw.add_watch(self.theme, "Warli")
        page = self.client.get("/watching").data.decode()
        self.assertIn("Then and now", page)
        self.assertIn('value="https://en.wikipedia.org/wiki/Warli"', page)

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

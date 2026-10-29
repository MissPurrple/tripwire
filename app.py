"""Tripwire's website. On your laptop, start it with:   python app.py
then open http://localhost:5000 in your browser.

Online (Vercel), it needs three settings (Environment Variables):
  DATABASE_URL     the address of a Postgres database
  TRIPWIRE_SECRET  a long random password that keeps people logged in safely
  CRON_SECRET      another long random password, so only Vercel's daily timer
                   can start a check (Vercel sends it automatically)
"""

import functools
import hmac
import os
import re
import time
from urllib.parse import urlparse

from flask import Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.exceptions import HTTPException

from tripwire import before, books, changes, charts, db, humancheck, i18n, live, mailer, shelf, sources, stats
from tripwire.checker import BOOK_FILE_DAYS, MAX_THEMES, SINGLE_LINKS, TooManyTries, Tripwire

ON_VERCEL = bool(os.environ.get("VERCEL"))

# Styling lives in public/static so Vercel can serve it directly; Flask serves
# the same folder when running on a laptop.
app = Flask(__name__, static_folder="public/static", static_url_path="/static")

secret = os.environ.get("TRIPWIRE_SECRET")
app.secret_key = secret or os.urandom(16)
SECRET_MISSING = ON_VERCEL and not secret
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=ON_VERCEL,
                  SESSION_COOKIE_HTTPONLY=True)

LOOKBACK_CHOICES = [(0, "From now on"), (7, "Changes in the last 7 days"),
                    (30, "Changes in the last 30 days"), (90, "Changes in the last 90 days")]

# How long one daily run may take, in seconds. Whatever doesn't fit waits for the next run.
CRON_BUDGET = int(os.environ.get("CRON_BUDGET_SECONDS", "50"))


def tw():
    if "tw" not in g:
        g.tw = Tripwire(app.config.get("DB_URL"))
    return g.tw


@app.teardown_appcontext
def close_db(_exc):
    if "tw" in g:
        g.tw.conn.close()


@app.before_request
def same_site_forms_only():
    """Refuse forms sent from another website. Otherwise a page elsewhere could quietly send a
    Tripwire form on a visitor's behalf (log them out, delete things...). Browsers stamp every
    form they send with where it came from (Sec-Fetch-Site and Origin); Tripwire checks the stamp."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    fetch_site = request.headers.get("Sec-Fetch-Site")
    if fetch_site:  # every modern browser: its own verdict, nothing to compare
        if fetch_site not in ("same-origin", "none"):
            abort(403)
        return
    origin = request.headers.get("Origin")  # older browsers: compare where the form came from
    if origin and (origin == "null" or urlparse(origin).netloc != request.host):
        abort(403)


@app.before_request
def load_user():
    if SECRET_MISSING and request.endpoint != "static":
        raise db.SetupError("Add a TRIPWIRE_SECRET setting in Vercel (Settings → Environment Variables, "
                            "any long random text), then redeploy.")
    g.user = tw().user(session["user_id"]) if "user_id" in session else None
    if g.user is None:
        session.pop("user_id", None)  # e.g. a guest whose things were tidied away
    g.guest = bool(g.user and g.user["guest"])


def safe_next(target, default):
    """Only ever a page on this site. Refuses tricks like //other.site or /\\other.site
    (browsers read a backslash as a slash) and hidden control characters."""
    if (target.startswith("/") and not target.startswith(("//", "/\\"))
            and not any(c == "\\" or ord(c) < 32 for c in target)):
        return target
    return default


def uid():
    """The visitor's id: their account, their guest visit, or 0 (nothing added yet, so nothing to show)."""
    return g.user["id"] if g.user else 0


@app.after_request
def protective_headers(response):
    """Ask browsers for the safe behaviour: no showing Tripwire inside other sites' frames
    (a trick to make people click things), no guessing file types, no telling other sites
    which Tripwire page someone came from, and no camera, microphone or location."""
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    return response


# Plain-words pages for "not found" and other errors, in the site's own style.
ERROR_PAGES = {
    400: ("BAD REQUEST", "That request didn't make sense to Tripwire. Try going back and doing it again."),
    403: ("ACCESS DENIED", "That isn't yours to see, or the link has expired."),
    404: ("FILE NOT FOUND", "There's nothing at this address. It may have moved, been deleted, or never existed."),
    405: ("WRONG KIND OF REQUEST", "That page can't be used like that. Try going back and using the buttons on the page."),
    413: ("TOO BIG", "That upload is too big. For books, try a smaller file (an EPUB or text version), or paste a link instead."),
}


@app.errorhandler(HTTPException)
def http_error(error):
    if error.code >= 500:
        return server_error(error)
    title, message = ERROR_PAGES.get(error.code, ("SOMETHING WENT WRONG", error.description or ""))
    if "user" not in g:
        g.user = None
    return render_template("error.html", code=error.code, title=title, message=message), error.code


@app.errorhandler(500)
def server_error(error):
    """Show a plain-words page instead of a bare 'Internal Server Error'.
    The full details still go to the logs (Vercel: the Logs tab)."""
    original = getattr(error, "original_exception", None)
    setup_problem = str(original) if isinstance(original, db.SetupError) else None
    # One short line, so the cause shows in Vercel's log list without opening the full traceback.
    app.logger.error("TRIPWIRE PROBLEM: %s", setup_problem or
                     (f"{type(original).__name__}: {str(original)[:300]}" if original else "unknown error"))
    if "user" not in g:
        g.user = None
    return render_template("error.html", code=500, setup_problem=setup_problem, title="SYSTEM ERROR",
                           message="Tripwire hit an unexpected problem. Nothing you did caused it."), 500


# Bots could otherwise make endless guest visits and fill the database. This counts guest
# visits for the whole site (nobody is tracked); people with an account are never affected.
GUESTS_PER_HOUR = int(os.environ.get("GUESTS_PER_HOUR", "300"))


def saves_things(view):
    """No account needed: the first time a visitor saves something, Tripwire starts a
    guest visit for them. It lasts until they close the browser, unless they make an account."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None and request.method == "POST":
            if tw().guests_this_hour() >= GUESTS_PER_HOUR:
                flash("Lots of visitors are trying Tripwire right now. Make an account (it's free) to carry on.", "error")
                return redirect(url_for("signup"))
            session["user_id"] = tw().create_guest()
            g.user, g.guest = tw().user(session["user_id"]), True
        return view(*args, **kwargs)
    return wrapped


GITHUB_URL = "https://github.com/MissPurrple/tripwire"


def current_language():
    lang = request.cookies.get("lang", i18n.DEFAULT)
    return lang if lang in i18n.LANGUAGES else i18n.DEFAULT


@app.context_processor
def language_helpers():
    lang = current_language()
    return {"lang": lang, "t": lambda text: i18n.translate(lang, text), "languages": i18n.choices(),
            "language_name": i18n.LANGUAGES[lang]["_english_name"], "github_url": GITHUB_URL}


@app.get("/language/<code>")
def set_language(code):
    """Switch language (remembered in this browser). Non-English languages are drafts: say so."""
    if code not in i18n.LANGUAGES:
        abort(404)
    target = request.args.get("next", "")
    response = redirect(safe_next(target, url_for("home")))
    response.set_cookie("lang", code, max_age=365 * 86400, samesite="Lax", secure=ON_VERCEL)
    if code != i18n.DEFAULT:
        session["language_popup"] = code
    return response


def account_required(why):
    """For the few things that need a real account (public copies, emails)."""
    def decorate(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None or g.guest:
                flash(f"Make an account to {why}. Everything you've added so far comes with you.", "message")
                return redirect(url_for("signup"))
            return view(*args, **kwargs)
        return wrapped
    return decorate


@app.context_processor
def helpers():
    return {"is_guest": g.get("guest", False),
            "CATEGORIES": changes.CATEGORIES, "details": db.entry_details, "donut": charts.donut,
            "CHANGE_KINDS": changes.CHANGE_KINDS, "kind_counts": changes.kind_counts,
            "KIND_SERIES": {key: n for n, key in enumerate(changes.CHANGE_KINDS, start=1)},
            "watch_link": watch_link}


def back_to(default="change_list"):
    """Where to go after Keep/Disregard. Only ever a page on this site."""
    target = request.form.get("next", "")
    return safe_next(target, url_for(default))


# -- accounts ---------------------------------------------------------------

HUMAN_CHECK_VALID_MINUTES = 15  # after passing the check, how long the sign-up form stays unlocked


def _new_human_check():
    session["human_check"] = humancheck.new_challenge(app.secret_key, app.config.get("HUMAN_CHECK_BITS",
                                                                                   humancheck.DEFAULT_BITS))
    session.pop("human_ok_at", None)
    return session["human_check"]


def _human_ok():
    return time.time() - session.get("human_ok_at", 0) < HUMAN_CHECK_VALID_MINUTES * 60


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if not _human_ok():
            flash("Please finish the human check first.", "error")
        else:
            email, password = request.form.get("email", ""), request.form.get("password", "")
            try:
                # A guest keeps everything they added: their visit becomes the account.
                user_id = tw().claim_guest(uid(), email, password) if g.guest else tw().create_user(email, password)
            except ValueError as exc:
                flash(str(exc), "error")
            else:
                session.clear()
                session.update(user_id=user_id, human_after_all=True, intro_seen=True)
                send_confirmation(tw().user(user_id), quietly=True)
                return redirect(url_for("welcome"))
    challenge = session.get("human_check") if _human_ok() else None
    if not _human_ok():
        challenge = _new_human_check()
    return render_template("auth.html", mode="signup", challenge=challenge, human_ok=_human_ok())


@app.post("/human-check")
def human_check():
    """The sign-up page's machine check: answer + background proof of work."""
    data = request.get_json(silent=True) or {}
    passed, message = humancheck.check(session.get("human_check"), app.secret_key, data.get("answer"),
                                       data.get("nonce"), data.get("website", ""),
                                       min_seconds=app.config.get("HUMAN_CHECK_MIN_SECONDS",
                                                                  humancheck.DEFAULT_MIN_SECONDS))
    if passed:
        session["human_ok_at"] = time.time()
        return jsonify(passed=True, message=message)
    challenge = _new_human_check()  # a wrong answer always gets a new question, so guessing doesn't help
    return jsonify(passed=False, message=message, question=challenge["question"], salt=challenge["salt"],
                   bits=challenge["bits"])


@app.get("/account")
@account_required("see and manage your account")
def account():
    conn, user_id = tw().conn, uid()
    def count(sql):
        return conn.execute(sql, (user_id,)).fetchone()["n"]
    counts = {"watching": count("SELECT COUNT(*) AS n FROM watches w JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ?"),
              "changes": count("""SELECT COUNT(*) AS n FROM entries e JOIN watches w ON w.id = e.watch_id
                                  JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ?"""),
              "shelf": count("SELECT COUNT(*) AS n FROM shelf_items WHERE user_id = ?"),
              "books": count("SELECT COUNT(*) AS n FROM book_comparisons WHERE user_id = ?")}
    every = Tripwire.summary_every(g.user)
    return render_template("account.html", counts=counts,
                           every="off" if every == "off" else f"{every} ({SUMMARY_WHEN[every]})")


@app.post("/account/delete")
@account_required("manage your account")
def delete_account():
    """The right to leave: the account and everything in it, gone. The password is asked
    again so nobody can do this with a borrowed, still-open browser."""
    try:
        right_password = tw().log_in(g.user["email"], request.form.get("password", ""))
    except TooManyTries as exc:
        flash(str(exc), "error")
        return redirect(url_for("account"))
    if request.form.get("sure") != "yes" or not right_password:
        flash("That password doesn't match, so nothing was deleted.", "error")
        return redirect(url_for("account"))
    tw().delete_user(uid())
    session.clear()
    session["intro_seen"] = True
    flash("Your account and everything in it have been deleted. Thank you for using Tripwire.")
    return redirect(url_for("home"))


@app.get("/welcome")
def welcome():
    """Shown once, straight after making an account."""
    if g.user is None or not session.pop("human_after_all", False):
        return redirect(url_for("home"))
    return render_template("human_after_all.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            user = tw().log_in(request.form.get("email", ""), request.form.get("password", ""))
        except TooManyTries as exc:
            flash(str(exc), "error")
            return render_template("auth.html", mode="login")
        if user:
            if g.guest:  # things added before logging in move into the account
                tw().merge_guest(uid(), user["id"])
            session.clear()
            session.update(user_id=user["id"], intro_seen=True)
            return redirect(url_for("home"))
        flash("That email and password don't match.", "error")
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    session["intro_seen"] = True  # straight back to the computer, not the opening again
    return redirect(url_for("home"))


# -- watching -----------------------------------------------------------------

@app.get("/")
def home():
    """The front page: a box computer that boots up, with the visitor's numbers
    and a menu. The first time in a visit, the opening plays first (add ?intro to see it again)."""
    intro = "intro" in request.args or not session.get("intro_seen")
    session["intro_seen"] = True
    numbers = None
    if g.user:
        user_id = uid()
        conn = tw().conn
        numbers = {
            "watching": conn.execute("""SELECT COUNT(*) AS n FROM watches w JOIN themes t ON t.id = w.theme_id
                                        WHERE t.user_id = ? AND w.kind != 'video'""", (user_id,)).fetchone()["n"],
            "videos": len(tw().videos(user_id)),
            "waiting": conn.execute("""SELECT COUNT(*) AS n FROM entries e JOIN watches w ON w.id = e.watch_id
                                       JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ? AND e.kept = 0""",
                                    (user_id,)).fetchone()["n"],
            "books": len(tw().book_comparisons(user_id)),
            "copies": len(tw().copies(user_id)),
            "shelf": conn.execute("SELECT COUNT(*) AS n FROM shelf_items WHERE user_id = ?", (user_id,)).fetchone()["n"],
        }
    return render_template("home.html", numbers=numbers, intro=intro)


@app.get("/watching")
def index():
    conn = tw().conn
    themes = conn.execute("SELECT * FROM themes WHERE user_id = ? ORDER BY loose, id", (uid(),)).fetchall()
    watches = {t["id"]: conn.execute("SELECT * FROM watches WHERE theme_id = ? ORDER BY label",
                                     (t["id"],)).fetchall() for t in themes}
    single = next((t for t in themes if t["loose"] == 1), None)
    themes = [t for t in themes if not t["loose"]]
    new_count = conn.execute("""SELECT COUNT(*) AS n FROM entries e JOIN watches w ON w.id = e.watch_id
                                JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ? AND e.kept = 0""",
                             (uid(),)).fetchone()["n"]
    return render_template("index.html", themes=themes, watches=watches, single=single,
                           single_name=SINGLE_LINKS, new_count=new_count,
                           max_themes=MAX_THEMES, lookback=LOOKBACK_CHOICES,
                           videos=tw().videos(uid()), video_state=_video_state,
                           tests=before.latest_by_url(conn, uid()), live_on="live" in request.args,
                           min_seconds=live.MIN_SECONDS_BETWEEN)


def watch_link(watch):
    """The address of something being watched, as a link."""
    if watch["kind"] == "wikipedia":
        from urllib.parse import quote
        return f"https://{watch['lang'] or 'en'}.wikipedia.org/wiki/{quote(watch['target'].replace(' ', '_'))}"
    return watch["target"]


@app.post("/themes")
@saves_things
def add_theme():
    try:
        tw().add_theme(uid(), request.form.get("name", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/themes/<int:theme_id>/delete")
def remove_theme(theme_id):
    theme = tw().own_theme(uid(), theme_id)
    if not theme or theme["loose"]:
        abort(404)
    tw().remove_theme(theme_id)
    flash("Theme removed.")
    return redirect(url_for("index"))


@app.post("/single-links")
@saves_things
def add_single_link():
    """Watch one link on its own, outside any theme."""
    return add_watch(tw().single_links(uid()))


@app.post("/themes/<int:theme_id>/watches")
@saves_things
def add_watch(theme_id):
    if not tw().own_theme(uid(), theme_id):
        abort(404)
    kind = request.form.get("kind") or None
    if sources.video_platform(request.form.get("target", "")):
        return add_video()  # a YouTube or TikTok link belongs on the Videos page
    try:
        tw().add_watch(theme_id, request.form.get("target", ""), kind=kind,
                       days_ago=int(request.form.get("days_ago") or 0))
        flash("Now watching it.")
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/watches/<int:watch_id>/delete")
def remove_watch(watch_id):
    if not tw().own_watch(uid(), watch_id):
        abort(404)
    tw().remove_watch(watch_id)
    return redirect(back_to("index"))


@app.post("/watches/<int:watch_id>/check")
def check_watch(watch_id):
    watch = tw().own_watch(uid(), watch_id)
    if not watch:
        abort(404)
    entry = tw().check_watch(watch)
    flash("Change found: see Changes." if entry else "Checked. Nothing new.")
    return redirect(back_to("index"))


@app.post("/check")
def check_all():
    started = time.monotonic()
    new = tw().check_all(uid(), deadline=started + 25)
    flash(f"Checked. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
    return redirect(url_for("change_list") if new else url_for("index"))


# -- videos -----------------------------------------------------------------

@app.get("/videos")
def videos():
    """Videos are on the Books & videos page now."""
    return redirect(url_for("book_list", _anchor="videos"))


@app.post("/videos/check")
def check_video():
    """Look at a video once, right now, without watching it."""
    url = request.form.get("target", "").strip()
    try:
        if not sources.video_platform(url):
            raise ValueError("Paste a YouTube or TikTok video link.")
        result = sources.video_status(url)
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("book_list", _anchor="videos"))
    return _books_page(video_result=result, video_url=url)


def _video_state(watch):
    import json
    return json.loads(watch["baseline_ref"] or "{}")


@app.post("/videos")
@saves_things
def add_video():
    try:
        tw().add_video(uid(), request.form.get("target", ""))
        flash("Now watching that video. It's on this page, under Videos.")
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("book_list", _anchor="videos"))
    return redirect(url_for("index", _anchor="videos"))


# -- live watching: one page, every few minutes, while the page is open -------

@app.get("/live")
def live_watch():
    """Live watch is at the top of the Watching page now."""
    return redirect(url_for("index", live=1, _anchor="live"))


@app.post("/live/check")
def live_check():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if "://" not in url:
        url = "https://" + url
    if not urlparse(url).netloc or sources.video_platform(url):
        return jsonify(error="Paste a link to a web page (videos belong on the Videos page)."), 400
    last = session.get("live_last_check", 0)
    if time.time() - last < live.MIN_SECONDS_BETWEEN - 5:  # a little slack for slow networks
        return jsonify(error="Checking too often. Tripwire waits at least a minute between checks."), 429
    session["live_last_check"] = time.time()
    try:
        return jsonify(live.check(url, data.get("previous")))
    except sources.SourceError as exc:
        return jsonify(error=str(exc)), 502


# -- changes ------------------------------------------------------------------

@app.get("/changes")
def change_list():
    category = request.args.get("category", "")
    query = """SELECT e.*, w.label, w.kind, w.target FROM entries e
               JOIN watches w ON w.id = e.watch_id JOIN themes t ON t.id = w.theme_id
               WHERE t.user_id = ?"""
    params = [uid()]
    if category:
        query += " AND e.category = ?"
        params.append(category)
    entries = tw().conn.execute(query + " ORDER BY e.detected_at DESC, e.id DESC", params).fetchall()
    every = Tripwire.summary_every(g.user) if g.user and not g.guest else "off"
    return render_template("changes.html", entries=entries, category=category,
                           s=tw().weekly_summary(uid(), _summary_period(g.user)),
                           email_ready=mailer.configured(), every=every, when=SUMMARY_WHEN)


@app.get("/entries/<int:entry_id>")
def entry(entry_id):
    row = tw().own_entry(uid(), entry_id)
    if row is None:
        flash("That change is no longer in your list.", "error")
        return redirect(url_for("change_list"))
    return render_template("entry.html", e=row, copy_options=tw().copy_options(entry=row),
                           copies=tw().copies(uid(), entry_id=entry_id))


@app.post("/entries/<int:entry_id>/keep")
def keep(entry_id):
    if not tw().own_entry(uid(), entry_id):
        abort(404)
    tw().keep(entry_id)
    return redirect(back_to())


@app.post("/entries/<int:entry_id>/disregard")
def disregard(entry_id):
    if not tw().own_entry(uid(), entry_id):
        abort(404)
    tw().disregard(entry_id)
    flash("Disregarded: that entry has been deleted.")
    return redirect(back_to())


# -- public Internet Archive copies: asked twice, never automatic ----------

def _copy_target(kind, item_id):
    """The entry or watched item a copy is for, and what could be saved."""
    if kind == "entry":
        item = tw().own_entry(uid(), item_id)
        options = tw().copy_options(entry=item) if item else []
    else:
        item = tw().own_watch(uid(), item_id)
        options = tw().copy_options(watch=item) if item else []
    if not item:
        abort(404)
    return item, options


@app.route("/copy/<kind>/<int:item_id>", methods=["GET", "POST"])
@account_required("keep public copies with the Internet Archive")
def public_copy(kind, item_id):
    """First ask: the button on a change or watched item brings people here.
    Second ask: this page says plainly what a public copy means, and only
    'Yes, save it' (with the box ticked) actually saves anything."""
    if kind not in ("entry", "watch"):
        abort(404)
    item, options = _copy_target(kind, item_id)
    if not options:
        flash("There's nothing that can be saved for this one.", "error")
        return redirect(url_for("index"))
    back = url_for("entry", entry_id=item_id) if kind == "entry" else url_for("index")

    if request.method == "POST":
        if request.form.get("understood") != "yes":
            flash("Tick the box to confirm you understand the copy will be public and permanent.", "error")
            return redirect(request.path)
        chosen = [o for i, o in enumerate(options) if request.form.get(f"option{i}")]
        if not chosen:
            flash("Choose at least one version to save.", "error")
            return redirect(request.path)
        saved = 0
        for option in chosen:
            try:
                tw().save_public_copy(uid(), option,
                                      watch_id=item["watch_id"] if kind == "entry" else item_id,
                                      entry_id=item_id if kind == "entry" else None)
                saved += 1
            except sources.SourceError as exc:
                flash(f"Couldn't save “{option['label']}”: {exc}", "error")
        if saved:
            flash(f"Saved {saved} public cop{'y' if saved == 1 else 'ies'} with the Internet Archive. "
                  "You'll find them on My shelf.")
        return redirect(back)

    return render_template("copy_confirm.html", item=item, kind=kind, options=options, back=back)


@app.get("/archive")
def archive():
    """Public Internet Archive copies are listed on My shelf now."""
    return redirect(url_for("shelf_page", _anchor="public-copies"))


# -- books: compare two editions, only when asked ---------------------------

# Vercel refuses uploads bigger than about 4.5 MB in total, so the page warns
# before sending; links have no such limit because Tripwire downloads them itself.
UPLOAD_LIMIT_MB = 4.4


def _edition(which):
    upload = request.files.get(f"file_{which}")
    link = request.form.get(f"link_{which}", "").strip()
    if upload and upload.filename:
        return {"name": books.clean_name(upload.filename), "data": upload.read()}
    return {"link": link}


@app.route("/books", methods=["GET", "POST"])
@saves_things
def book_list():
    """Books & videos: compare two editions of a book, and check a video."""
    if request.method == "POST":
        edition_a, edition_b = _edition("a"), _edition("b")
        try:
            job = tw().start_book_job(uid(), request.form.get("title", ""), edition_a, edition_b)
        except books.BookError as exc:
            flash(str(exc), "error")
        else:
            if request.form.get("watch_links"):
                _watch_book_links([e["link"] for e in (edition_a, edition_b) if e.get("link")])
            return redirect(url_for("book_job", job_id=job))
    return _books_page()


def _watch_book_links(links):
    """Keep watching the links a book came from (uploaded files can't be watched)."""
    for link in links:
        try:
            tw().add_watch(tw().single_links(uid()), link, kind="web")
            flash(f"Watching {link} too: it's on your Watching page.")
        except (ValueError, sources.SourceError) as exc:
            flash(f"Couldn't watch {link}: {exc}", "error")


def _books_page(**extra):
    tw().delete_book_files(older_than_days=BOOK_FILE_DAYS)
    # Arriving from elsewhere (not from within Books & videos): show the "entering" screen first.
    came_from = urlparse(request.referrer or "")
    intro = request.method == "GET" and not (came_from.netloc == request.host and came_from.path.startswith("/books"))
    return render_template("books.html", comparisons=tw().book_comparisons(uid()),
                           jobs=tw().book_jobs_in_progress(uid()), upload_limit_mb=UPLOAD_LIMIT_MB,
                           intro=intro, **extra)


@app.get("/books/jobs/<int:job_id>")
def book_job(job_id):
    """The waiting page. It starts the comparison, then checks on it until it's done."""
    job = tw().own_book_job(uid(), job_id)
    if not job:
        abort(404)
    if job["status"] == "done" and job["comparison_id"]:
        return redirect(url_for("book_comparison", comparison_id=job["comparison_id"]))
    return render_template("book_job.html", job=job)


def _job_json(job):
    return jsonify(status=job["status"], error=job["error"],
                   url=url_for("book_comparison", comparison_id=job["comparison_id"]) if job["comparison_id"] else None)


@app.post("/books/jobs/<int:job_id>/run")
def run_book_job(job_id):
    """Does the comparison. It keeps going on the server even if the page is closed."""
    if not tw().own_book_job(uid(), job_id):
        abort(404)
    return _job_json(tw().run_book_job(job_id))


@app.get("/books/jobs/<int:job_id>/status")
def book_job_status(job_id):
    job = tw().own_book_job(uid(), job_id)
    if not job:
        abort(404)
    return _job_json(job)


@app.post("/books/jobs/<int:job_id>/delete")
def delete_book_job(job_id):
    job = tw().own_book_job(uid(), job_id)
    if not job or job["status"] == "done":
        abort(404)
    tw().conn.execute("DELETE FROM book_jobs WHERE id = ?", (job_id,))
    return redirect(url_for("book_list"))


@app.get("/books/<int:comparison_id>")
def book_comparison(comparison_id):
    row = tw().own_book_comparison(uid(), comparison_id)
    if not row:
        abort(404)
    job = tw().book_job_for(comparison_id)
    return render_template("book.html", c=row, job=job, file_days=BOOK_FILE_DAYS,
                           kinds=changes.CHANGE_KINDS, interesting=_interesting_changes(db.entry_details(row)))


@app.post("/books/<int:comparison_id>/files/delete")
def delete_book_files(comparison_id):
    if not tw().own_book_comparison(uid(), comparison_id):
        abort(404)
    job = tw().book_job_for(comparison_id)
    if job:
        tw().delete_book_files(job_id=job["id"])
    flash("The book files are deleted. The comparison stays.")
    return redirect(url_for("book_comparison", comparison_id=comparison_id))


def _interesting_changes(d, limit=6):
    """A short list worth looking at first: renamed names, the most repeated
    word swaps, and the biggest added or removed passages. `d` is a comparison's details."""
    picks = [{"kind": "Name", "removed": old, "added": new} for old, new in d.get("name_changes", [])[:2]]
    picks += [{"kind": f"Swapped {n}×", "removed": old, "added": new}
              for old, new, n in d.get("swaps", [])[:2] if (old, new) not in {(p["removed"], p["added"]) for p in picks}]
    content = [h for h in d.get("hunks", []) if (h.get("kind") or changes.classify(h["removed"], h["added"])) == "content"]
    content.sort(key=lambda h: len((h["removed"] + " " + h["added"]).split()), reverse=True)
    picks += [{"kind": "Passage changed", "removed": h["removed"], "added": h["added"],
               "before": h["before"], "after": h["after"]} for h in content[:limit - len(picks)]]
    return picks[:limit]


@app.post("/books/<int:comparison_id>/delete")
def delete_book_comparison(comparison_id):
    if not tw().own_book_comparison(uid(), comparison_id):
        abort(404)
    tw().delete_book_comparison(comparison_id)
    flash("Comparison deleted.")
    return redirect(url_for("book_list"))


# ---------------------------------------------------------------------------
# My shelf: your own copies, and "Take my shelf with me"
# ---------------------------------------------------------------------------

def _shelf():
    return shelf.Shelf(tw().conn)


@app.get("/shelf")
def shelf_page():
    search = request.args.get("q", "")
    return render_template("shelf.html", items=_shelf().items(uid(), search), search=search,
                           used_mb=_shelf().used_bytes(uid()) / 1048576,
                           limit_mb=shelf.MAX_SHELF_BYTES // 1048576, upload_limit_mb=UPLOAD_LIMIT_MB,
                           copies=tw().copies(uid()))


@app.post("/shelf/file")
@saves_things
def shelf_add_file():
    upload = request.files.get("file")
    read_text = request.form.get("read_text", "").strip()
    try:
        item_id = _shelf().add_file(uid(), upload.filename if upload else request.form.get("file_name", ""),
                                    upload.read() if upload and upload.filename else b"",
                                    request.form.get("title", ""), request.form.get("note", ""),
                                    read_text=read_text or None, pages=request.form.get("read_pages", type=int))
    except shelf.ShelfError as exc:
        flash(str(exc), "error")
        return redirect(url_for("shelf_page"))
    flash("On your shelf." if upload and upload.filename else
          "On your shelf: its words are kept. The file itself was too big to upload, so keep your own copy of it.")
    return redirect(url_for("shelf_item", item_id=item_id))


@app.post("/shelf/link")
@saves_things
def shelf_add_link():
    url = request.form.get("url", "").strip()
    check_id = None
    try:
        if request.form.get("compare") and sources.is_wikipedia_link(url):
            try:
                check_id = before.save(tw().conn, uid(), before.compare_with_before(url))
            except sources.SourceError as exc:
                flash(f"Kept, but not compared with an older copy: {exc}")
        item_id = _shelf().add_link(uid(), url, request.form.get("title", ""), request.form.get("note", ""),
                                    before_check_id=check_id)
    except (shelf.ShelfError, sources.SourceError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("shelf_page"))
    if request.form.get("watch"):
        try:
            watch_id = tw().add_watch(tw().single_links(uid()), url)
            tw().conn.execute("UPDATE shelf_items SET watch_id = ? WHERE id = ?", (watch_id, item_id))
        except (ValueError, sources.SourceError) as exc:
            flash(f"Kept, but Tripwire couldn't start watching the link: {exc}")
    flash("On your shelf.")
    return redirect(url_for("shelf_item", item_id=item_id))


def _own_item(item_id):
    item = _shelf().item(uid(), item_id)
    if not item:
        abort(404)
    return item


@app.get("/shelf/<int:item_id>")
def shelf_item(item_id):
    item = _own_item(item_id)
    watch = tw().own_watch(uid(), item["watch_id"]) if item["watch_id"] else None
    check = before.own(tw().conn, uid(), item["before_check_id"]) if item["before_check_id"] else None
    return render_template("shelf_item.html", item=item, watch=watch, check=check,
                           check_details=db.entry_details(check) if check else None,
                           scan=bool(item["file_name"]) and not item["text"]
                           and item["file_name"].lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")))


# Kinds of file a browser can safely show inside the page; anything else is downloaded.
SHOW_INLINE = {"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif", "text/plain"}


def _download_headers(filename, inline=False):
    from urllib.parse import quote
    plain = re.sub(r'[^\w. -]', "_", filename, flags=re.ASCII) or "download"
    kind = "inline" if inline else "attachment"
    return {"Content-Disposition": f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff"}


def _stream(data):
    """Send in pieces: Vercel only allows big downloads when they're streamed."""
    for start in range(0, len(data), 256 * 1024):
        yield data[start:start + 256 * 1024]


@app.get("/shelf/<int:item_id>/file")
def shelf_file(item_id):
    found = _shelf().file(uid(), item_id)
    if not found:
        abort(404)
    name, data = found
    kind = shelf.file_type(name)
    inline = kind in SHOW_INLINE and not request.args.get("download")
    return Response(_stream(data), mimetype=kind if inline else "application/octet-stream",
                    headers=dict(_download_headers(name, inline), **{"Content-Length": str(len(data))}))


@app.post("/shelf/<int:item_id>/text")
def shelf_set_text(item_id):
    """Words read from a scan on the person's own device, sent afterwards."""
    _own_item(item_id)
    data = request.get_json(silent=True) or {}
    try:
        _shelf().set_text(uid(), item_id, data.get("text", ""), data.get("pages"))
    except shelf.ShelfError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True)


@app.post("/shelf/<int:item_id>/edit")
def shelf_edit(item_id):
    _own_item(item_id)
    _shelf().update(uid(), item_id, request.form.get("title", ""), request.form.get("note", ""))
    flash("Saved.")
    return redirect(url_for("shelf_item", item_id=item_id))


@app.post("/shelf/<int:item_id>/delete")
def shelf_delete(item_id):
    _own_item(item_id)
    _shelf().delete(uid(), item_id)
    flash("Removed from your shelf.")
    return redirect(url_for("shelf_page"))


@app.get("/shelf/download.<kind>")
def shelf_download(kind):
    """Take my shelf with me: everything as one ZIP (for any browser) or ZIM (for Kiwix)."""
    if kind not in ("zip", "zim"):
        abort(404)
    name = request.args.get("name", "").strip()[:80] or "My shelf"
    path = (_shelf().export_zip(uid(), name) if kind == "zip"
            else _shelf().export_zim(uid(), name, current_language()))
    size = os.path.getsize(path)

    def pieces():
        try:
            with open(path, "rb") as f:
                while True:
                    piece = f.read(256 * 1024)
                    if not piece:
                        break
                    yield piece
        finally:
            os.remove(path)

    filename = f"{shelf.safe_name(name, 'my-shelf')}-{db.now()[:10]}.{kind}"
    return Response(pieces(), mimetype="application/zip" if kind == "zip" else "application/octet-stream",
                    headers=dict(_download_headers(filename), **{"Content-Length": str(size)}))


# ---------------------------------------------------------------------------
# Compare with an older copy (Kiwix's offline Wikipedia, or the Internet Archive)
# ---------------------------------------------------------------------------

@app.route("/before", methods=["GET", "POST"])
@saves_things
def before_page():
    """The "Test against Kiwix" and "Test against Internet Archive" buttons."""
    if request.method == "GET":
        return redirect(url_for("index"))
    url, source = request.form.get("url", ""), request.form.get("source", "auto")
    back = safe_next(request.form.get("next", ""), url_for("index"))
    try:
        result = before.compare_with_before(url, source if source in ("kiwix", "archive") else "auto",
                                            kiwix_path=request.form.get("kiwix_path") or None,
                                            archive_url=request.form.get("archive_url") or None)
    except before.NotFound as exc:
        return render_template("before_similar.html", url=url, problem=str(exc), source=exc.source,
                               suggestions=exc.suggestions, back=back)
    except sources.SourceError as exc:
        flash(str(exc), "error")
        return redirect(back)
    return redirect(url_for("before_result", check_id=before.save(tw().conn, uid(), result)))


@app.get("/before/<int:check_id>")
def before_result(check_id):
    check = before.own(tw().conn, uid(), check_id)
    if not check:
        abort(404)
    d = db.entry_details(check)
    on_shelf = tw().conn.execute("SELECT id FROM shelf_items WHERE before_check_id = ? AND user_id = ?",
                                 (check_id, uid())).fetchone()
    return render_template("before_result.html", c=check, d=d, interesting=_interesting_changes(d, limit=8),
                           on_shelf=on_shelf)


@app.post("/before/<int:check_id>/shelf")
def before_to_shelf(check_id):
    check = before.own(tw().conn, uid(), check_id)
    if not check:
        abort(404)
    try:
        item_id = _shelf().add_link(uid(), check["url"], before_check_id=check_id)
    except (shelf.ShelfError, sources.SourceError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("before_result", check_id=check_id))
    flash("On your shelf, with this comparison.")
    return redirect(url_for("shelf_item", item_id=item_id))


@app.post("/before/<int:check_id>/delete")
def before_delete(check_id):
    if not before.own(tw().conn, uid(), check_id):
        abort(404)
    tw().conn.execute("DELETE FROM before_checks WHERE id = ?", (check_id,))
    return redirect(url_for("index"))

# What people see when they choose how often the summary is emailed.
SUMMARY_WHEN = {"daily": "every day", "weekly": "every Monday", "monthly": "on the 1st of each month"}


def _summary_period(user):
    every = Tripwire.summary_every(user) if user and not user["guest"] else "weekly"
    return every if every != "off" else "weekly"


@app.get("/summary")
def summary():
    """The summary is at the top of the Changes page now (emails still link here)."""
    return redirect(url_for("change_list", _anchor="summary"))


def site_url():
    """The site's address for links in emails: SITE_URL if set, otherwise the address being visited."""
    return (os.environ.get("SITE_URL") or request.url_root).rstrip("/")


def send_weekly_summary(user):
    """Email one person their daily, weekly or monthly summary. Raises mailer.MailError if it can't be sent."""
    s = tw().weekly_summary(user["id"], _summary_period(user))
    span = {"daily": "today", "weekly": "this week", "monthly": "this month"}[s["every"]]
    quiet = {"daily": "a quiet day", "weekly": "a quiet week", "monthly": "a quiet month"}[s["every"]]
    context = {"s": s, "site": site_url(), "email": user["email"], "CATEGORIES": changes.CATEGORIES,
               "span": span, "when": SUMMARY_WHEN[s["every"]]}
    subject = (f"Tripwire: {len(s['entries'])} change{'s' if len(s['entries']) != 1 else ''} {span}"
               if s["entries"] else f"Tripwire: {quiet}, nothing changed")
    mailer.send(user["email"], subject, render_template("emails/weekly.txt", **context),
                render_template("emails/weekly.html", **context))
    tw().mark_summary_sent(user["id"])


# -- confirming an email address: nothing is ever emailed to an address its owner hasn't confirmed,
#    so nobody can sign a stranger up and have Tripwire fill their inbox.

CONFIRM_DAYS = 3


def _confirm_signer():
    return URLSafeTimedSerializer(app.secret_key, salt="confirm-email")


def send_confirmation(user, quietly=False):
    """Email a "confirm your address" link. At most once every few minutes per account."""
    if not mailer.configured() or user["email_confirmed_at"]:
        return
    if not tw().can_send_confirmation(user):
        if not quietly:
            flash(f"One was sent in the last {Tripwire.CONFIRM_RESEND_MINUTES} minutes. Check your inbox (and spam).", "error")
        return
    link = site_url() + url_for("confirm_email", token=_confirm_signer().dumps({"id": user["id"], "email": user["email"]}))
    try:
        mailer.send(user["email"], "Tripwire: please confirm your email address",
                    render_template("emails/confirm.txt", link=link, days=CONFIRM_DAYS),
                    render_template("emails/confirm.html", link=link, days=CONFIRM_DAYS))
    except mailer.MailError as exc:
        if not quietly:
            flash(str(exc), "error")
        return
    tw().mark_confirmation_sent(user["id"])
    if not quietly:
        flash(f"Sent to {user['email']}. Open the link in it to confirm (check spam if it doesn't arrive).")


@app.post("/confirm/send")
@account_required("get summaries by email")
def send_confirmation_again():
    send_confirmation(g.user)
    return redirect(url_for("change_list", _anchor="summary"))


@app.route("/confirm/<token>", methods=["GET", "POST"])
def confirm_email(token):
    """The link from the email. It opens a page with a button (rather than confirming straight
    away), because some email services open links by themselves to check them."""
    try:
        data = _confirm_signer().loads(token, max_age=CONFIRM_DAYS * 86400)
    except BadSignature:
        return render_template("confirm.html", state="bad", days=CONFIRM_DAYS)
    if request.method == "POST":
        if tw().confirm_email(data.get("id"), data.get("email")):
            flash("Thank you: your email address is confirmed. Summary emails can now reach you.")
            return redirect(url_for("change_list", _anchor="summary") if g.user and g.user["id"] == data.get("id") else url_for("home"))
        return render_template("confirm.html", state="bad", days=CONFIRM_DAYS)
    return render_template("confirm.html", state="ask", email=data.get("email", ""), token=token)


@app.post("/summary/email")
@account_required("get summaries by email")
def summary_email_setting():
    every = request.form.get("every", "off")
    try:
        tw().set_summary_every(uid(), every)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Summary emails are on: one {SUMMARY_WHEN[every]}." if every != "off" else "Summary emails are off.")
    return redirect(url_for("change_list", _anchor="summary"))


@app.post("/summary/send")
@account_required("get summaries by email")
def summary_send_now():
    """Send the summary straight away (handy for a demo). At most once every 2 minutes."""
    if not g.user["email_confirmed_at"]:
        flash("Confirm your email address first (the button is on this page).", "error")
        return redirect(url_for("change_list", _anchor="summary"))
    if time.time() - session.get("summary_sent_now", 0) < 120:
        flash("One just went out. Give it a couple of minutes before sending another.", "error")
        return redirect(url_for("change_list", _anchor="summary"))
    try:
        send_weekly_summary(g.user)
    except mailer.MailError as exc:
        flash(str(exc), "error")
    else:
        session["summary_sent_now"] = time.time()
        flash(f"Sent to {g.user['email']}. It can take a minute to arrive (check spam the first time).")
    return redirect(url_for("change_list", _anchor="summary"))


# -- about, feedback, and demo data ------------------------------------------

def is_admin():
    """The site owner(s): emails listed in the ADMIN_EMAILS setting (comma-separated)."""
    admins = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    return bool(g.user) and g.user["email"].lower() in admins


@app.context_processor
def admin_flag():
    return {"is_admin": is_admin() if "user" in g else False}


FEEDBACK_AREAS = ["Watching pages", "Changes", "Videos", "Books", "Stats", "Public copies", "Summary emails",
                  "The look and feel", "Something else"]


@app.get("/about")
def about():
    return render_template("about.html", totals=tw().totals(), areas=FEEDBACK_AREAS)


@app.post("/feedback")
def send_feedback():
    if request.form.get("website"):  # the hidden trap field: a bot, so quietly ignore it
        return redirect(url_for("about") + "#feedback")
    try:
        tw().add_feedback(uid() or None, request.form.get("tried"), request.form.get("message"))
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash("Thank you! Your feedback went straight to the person who made Tripwire.")
    return redirect(url_for("about") + "#feedback")


@app.get("/feedback")
def feedback_inbox():
    if not is_admin():
        abort(404)
    return render_template("feedback.html", feedback=tw().all_feedback())


@app.post("/demo-data")
@saves_things
def demo_data():
    """Fill the signed-in person's own account with real examples (for pitches and exploring)."""
    report = tw().add_demo_data(uid(), deadline=time.monotonic() + 40)
    parts = [f"{report['watched']} Wikipedia article{'s' if report['watched'] != 1 else ''} added"
             f" ({report['changes']} with changes in the last 90 days)"]
    if report["video"]:
        parts.append("a video")
    message = "Examples added: " + ", ".join(parts) + "."
    if report["unfinished"]:
        message += " Some were left out to keep this quick: press the button again to add the rest."
    if report["problems"]:
        message += " Couldn't add: " + "; ".join(report["problems"][:3]) + "."
    flash(message, "error" if report["problems"] and not report["watched"] else "message")
    if report["book_job"]:
        return redirect(url_for("book_job", job_id=report["book_job"]))
    return redirect(url_for("index"))


# -- stats --------------------------------------------------------------------

# The Stats page groups the kinds of change into a few slices.
CHANGE_GROUPS = {"small": "Tiny & formatting", "edit": "Edits", "major": "Major changes",
                 "removal": "Removals & removed pages", "video": "Video changes"}
GROUP_OF = {"formatting": "small", "tiny": "small", "edit": "edit", "major": "major", "removal": "removal",
            "page_removed": "removal", "video_retitled": "video", "video_restricted": "video", "video_gone": "video"}


def _my_numbers(user_id):
    """The signed-in person's own Tripwire numbers."""
    from datetime import datetime, timedelta, timezone
    rows = tw().conn.execute("""SELECT e.category, e.detected_at FROM entries e JOIN watches w ON w.id = e.watch_id
                                JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ?""", (user_id,)).fetchall()
    groups = dict.fromkeys(CHANGE_GROUPS, 0)
    for r in rows:
        groups[GROUP_OF.get(r["category"], "edit")] += 1
    today = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())
    weeks = [week_start - timedelta(weeks=n) for n in range(11, -1, -1)]
    per_week = dict.fromkeys(weeks, 0)
    for r in rows:
        day = datetime.strptime(r["detected_at"][:10], "%Y-%m-%d").date()
        monday = day - timedelta(days=day.weekday())
        if monday in per_week:
            per_week[monday] += 1
    top = charts.nice_max(max(per_week.values()) or 1)
    bars = [{"label": w.strftime("%d %b"), "count": n, "height": 100 * n / top} for w, n in per_week.items()]
    swaps, comparisons = {}, tw().book_comparisons(user_id)
    for c in comparisons:
        for old, new, n in db.entry_details(c).get("swaps", []):
            swaps[(old, new)] = swaps.get((old, new), 0) + n
    top_swaps = sorted(swaps.items(), key=lambda item: -item[1])[:3]
    watched = tw().conn.execute("""SELECT COUNT(*) AS n FROM watches w JOIN themes t ON t.id = w.theme_id
                                   WHERE t.user_id = ?""", (user_id,)).fetchone()["n"]
    return {"groups": groups, "total": len(rows), "bars": bars, "bar_top": top, "watched": watched,
            "comparisons": len(comparisons), "swap_total": sum(swaps.values()), "top_swaps": top_swaps,
            "copies": len(tw().copies(user_id))}


@app.get("/stats")
def stats_page():
    st = stats.Stats(tw().conn)
    traffic = st.traffic()
    book_data = st.books()
    missing, total_years = st.book_years_needed()
    traffic_chart = None
    if traffic.get("series") and traffic["series"]["timestamps"]:
        labels = [t[5:10].replace("-", "/") for t in traffic["series"]["timestamps"]]
        traffic_chart = charts.line_chart(labels, [("human", "Humans", traffic["series"]["human"]),
                                                   ("bot", "Bots", traffic["series"]["bot"])],
                                          y_suffix="%", y_max=100, x_ticks=7)
    books_chart = None
    if book_data:
        books_chart = charts.line_chart(book_data["years"], [
            ("all", "All books", book_data["running"]["all"]),
            ("print", "Print only", book_data["running"]["print"]),
            ("digital", "With a digital copy", book_data["running"]["digital"])], width=520, height=280)
    # The world estimate is in the hundreds of millions, so it gets its own chart
    # (on Open Library's chart it would flatten those lines).
    world = stats.Stats.world_estimate()
    world_chart = charts.line_chart(world["years"], [("world", "World estimate (middle)", world["mid"])],
                                    width=520, height=240, x_ticks=6,
                                    band={"series": 4, "low": world["low"], "high": world["high"],
                                          "label": "Range"},
                                    markers=[{"index": 0, "value": stats.GOOGLE_2010_COUNT,
                                              "label": "Google's 2010 count: 129,864,880"}],
                                    series_offset=3, y_min=100_000_000)
    return render_template("stats.html", traffic=traffic, traffic_chart=traffic_chart, books=book_data,
                           books_chart=books_chart, world=world, world_chart=world_chart,
                           books_missing=len(missing), books_total=total_years,
                           world_sources=stats.WORLD_SOURCES, google_count=stats.GOOGLE_2010_COUNT,
                           per_year_low=stats.NEW_TITLES_PER_YEAR_LOW, per_year_high=stats.NEW_TITLES_PER_YEAR_HIGH,
                           mine=_my_numbers(uid()), groups=CHANGE_GROUPS)


@app.get("/stats/traffic.json")
def stats_traffic():
    traffic = stats.Stats(tw().conn).traffic()
    return jsonify({k: traffic.get(k) for k in ("connected", "human", "bot", "updated", "error", "stale")})


@app.post("/stats/books/fill")
def stats_fill_books():
    """Fetch a few more years of book counts. The Stats page calls this until all are in."""
    missing, total, problem = stats.Stats(tw().conn).fill_book_years(seconds=20)
    return jsonify(missing=missing, total=total, problem=problem)


# -- the daily check (called by Vercel's timer, see vercel.json) -------------

@app.get("/cron")
def cron():
    expected = os.environ.get("CRON_SECRET")
    given = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(given, f"Bearer {expected}"):
        abort(403)
    started = time.monotonic()
    # Internet Archive copies are never made automatically: people choose.
    new = tw().check_all(deadline=started + CRON_BUDGET * 0.5)
    # Finish any book comparison that was interrupted, then tidy up old book files.
    finished = 0
    for job in tw().unfinished_book_jobs():
        if time.monotonic() > started + CRON_BUDGET:
            break
        tw().run_book_job(job["id"])
        finished += 1
    tw().delete_book_files(older_than_days=BOOK_FILE_DAYS)
    tw().delete_old_guests()
    # Summary emails: daily ones every day, weekly ones on Mondays, monthly ones on the 1st.
    emailed = 0
    if mailer.configured():
        for user in tw().users_due_summary():
            if time.monotonic() > started + CRON_BUDGET:
                break
            try:
                send_weekly_summary(user)
                emailed += 1
            except mailer.MailError:
                app.logger.exception("Weekly summary email to user %s failed", user["id"])
    remaining = started + CRON_BUDGET - time.monotonic()
    if remaining > 5:
        stats.Stats(tw().conn).fill_book_years(seconds=remaining - 3)
    return jsonify(new_entries=len(new), book_jobs_run=finished, summaries_emailed=emailed,
                   seconds=round(time.monotonic() - started, 1))


if __name__ == "__main__":
    app.run(debug=True)

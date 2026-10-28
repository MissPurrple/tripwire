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
import time

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from tripwire import books, changes, db, sources
from tripwire.checker import MAX_THEMES, SINGLE_LINKS, Tripwire

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
def load_user():
    if SECRET_MISSING and request.endpoint != "static":
        raise db.SetupError("Add a TRIPWIRE_SECRET setting in Vercel (Settings → Environment Variables, "
                            "any long random text), then redeploy.")
    g.user = tw().user(session["user_id"]) if "user_id" in session else None


@app.errorhandler(500)
def server_error(error):
    """Show a plain-words page instead of a bare 'Internal Server Error'.
    The full details still go to the logs (Vercel: the Logs tab)."""
    original = getattr(error, "original_exception", None)
    setup_problem = str(original) if isinstance(original, db.SetupError) else None
    if "user" not in g:
        g.user = None
    return render_template("error.html", setup_problem=setup_problem), 500


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def helpers():
    return {"CATEGORIES": changes.CATEGORIES, "details": db.entry_details}


def back_to(default="change_list"):
    """Where to go after Keep/Disregard. Only ever a page on this site."""
    target = request.form.get("next", "")
    return target if target.startswith("/") and not target.startswith("//") else url_for(default)


# -- accounts ---------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            user_id = tw().create_user(request.form.get("email", ""), request.form.get("password", ""))
        except ValueError as exc:
            flash(str(exc), "error")
        else:
            session.clear()
            session["user_id"] = user_id
            return redirect(url_for("index"))
    return render_template("auth.html", mode="signup")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = tw().log_in(request.form.get("email", ""), request.form.get("password", ""))
        if user:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        flash("That email and password don't match.", "error")
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# -- watching -----------------------------------------------------------------

@app.get("/")
@login_required
def index():
    conn = tw().conn
    themes = conn.execute("SELECT * FROM themes WHERE user_id = ? ORDER BY loose, id", (g.user["id"],)).fetchall()
    watches = {t["id"]: conn.execute("SELECT * FROM watches WHERE theme_id = ? ORDER BY label",
                                     (t["id"],)).fetchall() for t in themes}
    single = next((t for t in themes if t["loose"] == 1), None)
    themes = [t for t in themes if not t["loose"]]
    new_count = conn.execute("""SELECT COUNT(*) AS n FROM entries e JOIN watches w ON w.id = e.watch_id
                                JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ? AND e.kept = 0""",
                             (g.user["id"],)).fetchone()["n"]
    return render_template("index.html", themes=themes, watches=watches, single=single,
                           single_name=SINGLE_LINKS, new_count=new_count,
                           max_themes=MAX_THEMES, lookback=LOOKBACK_CHOICES)


@app.post("/themes")
@login_required
def add_theme():
    try:
        tw().add_theme(g.user["id"], request.form.get("name", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/themes/<int:theme_id>/delete")
@login_required
def remove_theme(theme_id):
    theme = tw().own_theme(g.user["id"], theme_id)
    if not theme or theme["loose"]:
        abort(404)
    tw().remove_theme(theme_id)
    flash("Theme removed.")
    return redirect(url_for("index"))


@app.post("/single-links")
@login_required
def add_single_link():
    """Watch one link on its own, outside any theme."""
    return add_watch(tw().single_links(g.user["id"]))


@app.post("/themes/<int:theme_id>/watches")
@login_required
def add_watch(theme_id):
    if not tw().own_theme(g.user["id"], theme_id):
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
@login_required
def remove_watch(watch_id):
    if not tw().own_watch(g.user["id"], watch_id):
        abort(404)
    tw().remove_watch(watch_id)
    return redirect(back_to("index"))


@app.post("/watches/<int:watch_id>/check")
@login_required
def check_watch(watch_id):
    watch = tw().own_watch(g.user["id"], watch_id)
    if not watch:
        abort(404)
    entry = tw().check_watch(watch)
    flash("Change found: see Changes." if entry else "Checked. Nothing new.")
    return redirect(back_to("index"))


@app.post("/check")
@login_required
def check_all():
    started = time.monotonic()
    new = tw().check_all(g.user["id"], deadline=started + 25)
    flash(f"Checked. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
    return redirect(url_for("change_list") if new else url_for("index"))


# -- videos -----------------------------------------------------------------

@app.get("/videos")
@login_required
def videos():
    return render_template("videos.html", videos=tw().videos(g.user["id"]), state=_video_state)


def _video_state(watch):
    import json
    return json.loads(watch["baseline_ref"] or "{}")


@app.post("/videos")
@login_required
def add_video():
    try:
        tw().add_video(g.user["id"], request.form.get("target", ""))
        flash("Now watching that video. Find it on the Videos page.")
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("videos"))


# -- changes ------------------------------------------------------------------

@app.get("/changes")
@login_required
def change_list():
    category = request.args.get("category", "")
    query = """SELECT e.*, w.label, w.kind, w.target FROM entries e
               JOIN watches w ON w.id = e.watch_id JOIN themes t ON t.id = w.theme_id
               WHERE t.user_id = ?"""
    params = [g.user["id"]]
    if category:
        query += " AND e.category = ?"
        params.append(category)
    entries = tw().conn.execute(query + " ORDER BY e.detected_at DESC, e.id DESC", params).fetchall()
    return render_template("changes.html", entries=entries, category=category)


@app.get("/entries/<int:entry_id>")
@login_required
def entry(entry_id):
    row = tw().own_entry(g.user["id"], entry_id)
    if row is None:
        flash("That change is no longer in your list.", "error")
        return redirect(url_for("change_list"))
    return render_template("entry.html", e=row, copy_options=tw().copy_options(entry=row),
                           copies=tw().copies(g.user["id"], entry_id=entry_id))


@app.post("/entries/<int:entry_id>/keep")
@login_required
def keep(entry_id):
    if not tw().own_entry(g.user["id"], entry_id):
        abort(404)
    tw().keep(entry_id)
    return redirect(back_to())


@app.post("/entries/<int:entry_id>/disregard")
@login_required
def disregard(entry_id):
    if not tw().own_entry(g.user["id"], entry_id):
        abort(404)
    tw().disregard(entry_id)
    flash("Disregarded: that entry has been deleted.")
    return redirect(back_to())


# -- public Internet Archive copies: asked twice, never automatic ----------

def _copy_target(kind, item_id):
    """The entry or watched item a copy is for, and what could be saved."""
    if kind == "entry":
        item = tw().own_entry(g.user["id"], item_id)
        options = tw().copy_options(entry=item) if item else []
    else:
        item = tw().own_watch(g.user["id"], item_id)
        options = tw().copy_options(watch=item) if item else []
    if not item:
        abort(404)
    return item, options


@app.route("/copy/<kind>/<int:item_id>", methods=["GET", "POST"])
@login_required
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
                tw().save_public_copy(g.user["id"], option,
                                      watch_id=item["watch_id"] if kind == "entry" else item_id,
                                      entry_id=item_id if kind == "entry" else None)
                saved += 1
            except sources.SourceError as exc:
                flash(f"Couldn't save “{option['label']}”: {exc}", "error")
        if saved:
            flash(f"Saved {saved} public cop{'y' if saved == 1 else 'ies'} with the Internet Archive. "
                  "You'll find them in the Archive tab.")
        return redirect(back)

    return render_template("copy_confirm.html", item=item, kind=kind, options=options, back=back)


@app.get("/archive")
@login_required
def archive():
    """Every public Internet Archive copy this person chose to make."""
    return render_template("archive.html", copies=tw().copies(g.user["id"]))


# -- books: compare two editions, only when asked ---------------------------

# Vercel refuses uploads bigger than about 4.5 MB in total, so the page warns
# before sending; links have no such limit because Tripwire downloads them itself.
UPLOAD_LIMIT_MB = 4.4


def _edition(which):
    upload = request.files.get(f"file_{which}")
    link = request.form.get(f"link_{which}", "").strip()
    if upload and upload.filename:
        return upload.filename, upload.read()
    if link:
        return books.fetch_book(link)
    raise books.BookError(f"Add edition {which.upper()}: upload a file or paste a link.")


@app.route("/books", methods=["GET", "POST"])
@login_required
def book_list():
    if request.method == "POST":
        try:
            comparison = tw().compare_books(g.user["id"], request.form.get("title", ""),
                                            _edition("a"), _edition("b"))
        except (books.BookError, sources.SourceError) as exc:
            flash(str(exc), "error")
        else:
            return redirect(url_for("book_comparison", comparison_id=comparison))
    return render_template("books.html", comparisons=tw().book_comparisons(g.user["id"]),
                           upload_limit_mb=UPLOAD_LIMIT_MB)


@app.get("/books/<int:comparison_id>")
@login_required
def book_comparison(comparison_id):
    row = tw().own_book_comparison(g.user["id"], comparison_id)
    if not row:
        abort(404)
    return render_template("book.html", c=row)


@app.post("/books/<int:comparison_id>/delete")
@login_required
def delete_book_comparison(comparison_id):
    if not tw().own_book_comparison(g.user["id"], comparison_id):
        abort(404)
    tw().delete_book_comparison(comparison_id)
    flash("Comparison deleted.")
    return redirect(url_for("book_list"))


@app.get("/summary")
@login_required
def summary():
    return render_template("summary.html", s=tw().weekly_summary(g.user["id"]))


# -- the daily check (called by Vercel's timer, see vercel.json) -------------

@app.get("/cron")
def cron():
    expected = os.environ.get("CRON_SECRET")
    given = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(given, f"Bearer {expected}"):
        abort(403)
    started = time.monotonic()
    # Internet Archive copies are no longer made automatically: people will choose.
    new = tw().check_all(deadline=started + CRON_BUDGET)
    return jsonify(new_entries=len(new),
                   seconds=round(time.monotonic() - started, 1))


if __name__ == "__main__":
    app.run(debug=True)

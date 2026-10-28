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

from tripwire import changes, db, sources
from tripwire.checker import MAX_THEMES, Tripwire

ON_VERCEL = bool(os.environ.get("VERCEL"))

# Styling lives in public/static so Vercel can serve it directly; Flask serves
# the same folder when running on a laptop.
app = Flask(__name__, static_folder="public/static", static_url_path="/static")

secret = os.environ.get("TRIPWIRE_SECRET")
if ON_VERCEL and not secret:
    raise RuntimeError("Add a TRIPWIRE_SECRET environment variable in Vercel (any long random text).")
app.secret_key = secret or os.urandom(16)
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=ON_VERCEL,
                  SESSION_COOKIE_HTTPONLY=True)

LOOKBACK_CHOICES = [(0, "From now on"), (7, "Changes in the last 7 days"),
                    (30, "Changes in the last 30 days"), (90, "Changes in the last 90 days")]

# How long one daily run may take, in seconds. Whatever doesn't fit waits for the next run.
CRON_BUDGET = int(os.environ.get("CRON_BUDGET_SECONDS", "50"))


def tw():
    if "tw" not in g:
        g.tw = Tripwire(app.config.get("DB_URL"), archive=app.config.get("ARCHIVE", True))
    return g.tw


@app.teardown_appcontext
def close_db(_exc):
    if "tw" in g:
        g.tw.conn.close()


@app.before_request
def load_user():
    g.user = tw().user(session["user_id"]) if "user_id" in session else None


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


def back_to(default="archive"):
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
    themes = conn.execute("SELECT * FROM themes WHERE user_id = ? ORDER BY id", (g.user["id"],)).fetchall()
    watches = {t["id"]: conn.execute("SELECT * FROM watches WHERE theme_id = ? ORDER BY label",
                                     (t["id"],)).fetchall() for t in themes}
    new_count = conn.execute("""SELECT COUNT(*) AS n FROM entries e JOIN watches w ON w.id = e.watch_id
                                JOIN themes t ON t.id = w.theme_id WHERE t.user_id = ? AND e.kept = 0""",
                             (g.user["id"],)).fetchone()["n"]
    return render_template("index.html", themes=themes, watches=watches, new_count=new_count,
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
    if not tw().own_theme(g.user["id"], theme_id):
        abort(404)
    tw().remove_theme(theme_id)
    flash("Theme removed.")
    return redirect(url_for("index"))


@app.post("/themes/<int:theme_id>/watches")
@login_required
def add_watch(theme_id):
    if not tw().own_theme(g.user["id"], theme_id):
        abort(404)
    kind = request.form.get("kind") or None
    try:
        tw().add_watch(theme_id, request.form.get("target", ""), kind=kind,
                       days_ago=int(request.form.get("days_ago") or 0))
        flash("Now watching it. A copy of today's version will be saved to the Internet Archive on the next check.")
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/watches/<int:watch_id>/delete")
@login_required
def remove_watch(watch_id):
    if not tw().own_watch(g.user["id"], watch_id):
        abort(404)
    tw().remove_watch(watch_id)
    return redirect(url_for("index"))


@app.post("/watches/<int:watch_id>/check")
@login_required
def check_watch(watch_id):
    watch = tw().own_watch(g.user["id"], watch_id)
    if not watch:
        abort(404)
    entry = tw().check_watch(watch)
    flash("Change found and saved to your archive." if entry else "Checked. Nothing new.")
    return redirect(url_for("index"))


@app.post("/check")
@login_required
def check_all():
    started = time.monotonic()
    new = tw().check_all(g.user["id"], deadline=started + 25)
    tw().save_archive_copies(deadline=started + 40)
    flash(f"Checked. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
    return redirect(url_for("archive") if new else url_for("index"))


# -- the archive --------------------------------------------------------------

@app.get("/archive")
@login_required
def archive():
    category = request.args.get("category", "")
    query = """SELECT e.*, w.label, w.kind, w.target FROM entries e
               JOIN watches w ON w.id = e.watch_id JOIN themes t ON t.id = w.theme_id
               WHERE t.user_id = ?"""
    params = [g.user["id"]]
    if category:
        query += " AND e.category = ?"
        params.append(category)
    entries = tw().conn.execute(query + " ORDER BY e.detected_at DESC, e.id DESC", params).fetchall()
    return render_template("archive.html", entries=entries, category=category)


@app.get("/entries/<int:entry_id>")
@login_required
def entry(entry_id):
    row = tw().own_entry(g.user["id"], entry_id)
    if row is None:
        flash("That archive entry no longer exists.", "error")
        return redirect(url_for("archive"))
    return render_template("entry.html", e=row)


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
    new = tw().check_all(deadline=started + CRON_BUDGET * 0.6)
    saved = tw().save_archive_copies(deadline=started + CRON_BUDGET)
    return jsonify(new_entries=len(new), archive_copies_saved=saved,
                   seconds=round(time.monotonic() - started, 1))


if __name__ == "__main__":
    app.run(debug=True)

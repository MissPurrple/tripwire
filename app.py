"""Tripwire's web page. Start it with:   python app.py
then open http://localhost:5000 in your browser."""

import os

from flask import Flask, flash, g, redirect, render_template, request, url_for

from tripwire import changes, db, sources
from tripwire.checker import MAX_THEMES, Tripwire

app = Flask(__name__)
app.secret_key = os.environ.get("TRIPWIRE_SECRET", os.urandom(16))

LOOKBACK_CHOICES = [(0, "From now on"), (7, "Changes in the last 7 days"),
                    (30, "Changes in the last 30 days"), (90, "Changes in the last 90 days")]


def tw():
    if "tw" not in g:
        g.tw = Tripwire(app.config.get("DB_PATH"), archive=app.config.get("ARCHIVE", True))
    return g.tw


@app.teardown_appcontext
def close_db(_exc):
    if "tw" in g:
        g.tw.conn.close()


@app.context_processor
def helpers():
    return {"CATEGORIES": changes.CATEGORIES, "details": db.entry_details}


def back_to(default="archive"):
    """Where to go after Keep/Disregard. Only ever a page on this site."""
    target = request.form.get("next", "")
    return target if target.startswith("/") and not target.startswith("//") else url_for(default)


@app.get("/")
def index():
    conn = tw().conn
    themes = conn.execute("SELECT * FROM themes ORDER BY id").fetchall()
    watches = {t["id"]: conn.execute("SELECT * FROM watches WHERE theme_id = ? ORDER BY label",
                                     (t["id"],)).fetchall() for t in themes}
    new_count = conn.execute("SELECT COUNT(*) FROM entries WHERE kept = 0").fetchone()[0]
    return render_template("index.html", themes=themes, watches=watches, new_count=new_count,
                           max_themes=MAX_THEMES, lookback=LOOKBACK_CHOICES)


@app.post("/themes")
def add_theme():
    try:
        tw().add_theme(request.form.get("name", ""))
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/themes/<int:theme_id>/delete")
def remove_theme(theme_id):
    tw().remove_theme(theme_id)
    flash("Theme removed.")
    return redirect(url_for("index"))


@app.post("/themes/<int:theme_id>/watches")
def add_watch(theme_id):
    kind = request.form.get("kind") or None
    try:
        tw().add_watch(theme_id, request.form.get("target", ""), kind=kind,
                       days_ago=int(request.form.get("days_ago") or 0))
        flash("Now watching it. A copy of today's version is being saved to the Internet Archive.")
    except (ValueError, sources.SourceError) as exc:
        flash(str(exc), "error")
    return redirect(url_for("index"))


@app.post("/watches/<int:watch_id>/delete")
def remove_watch(watch_id):
    tw().remove_watch(watch_id)
    return redirect(url_for("index"))


@app.post("/watches/<int:watch_id>/check")
def check_watch(watch_id):
    entry = tw().check_watch(watch_id)
    flash("Change found and saved to your archive." if entry else "Checked. Nothing new.")
    return redirect(url_for("index"))


@app.post("/check")
def check_all():
    new = tw().check_all()
    flash(f"Checked everything. {len(new)} new archive entr{'y' if len(new) == 1 else 'ies'}.")
    return redirect(url_for("archive") if new else url_for("index"))


@app.get("/archive")
def archive():
    category = request.args.get("category", "")
    query = """SELECT e.*, w.label, w.kind, w.target FROM entries e
               JOIN watches w ON w.id = e.watch_id"""
    params = ()
    if category:
        query += " WHERE e.category = ?"
        params = (category,)
    entries = tw().conn.execute(query + " ORDER BY e.detected_at DESC, e.id DESC", params).fetchall()
    return render_template("archive.html", entries=entries, category=category)


@app.get("/entries/<int:entry_id>")
def entry(entry_id):
    row = tw().conn.execute("""SELECT e.*, w.label, w.kind, w.target FROM entries e
                               JOIN watches w ON w.id = e.watch_id WHERE e.id = ?""",
                            (entry_id,)).fetchone()
    if row is None:
        flash("That archive entry no longer exists.", "error")
        return redirect(url_for("archive"))
    return render_template("entry.html", e=row)


@app.post("/entries/<int:entry_id>/keep")
def keep(entry_id):
    tw().keep(entry_id)
    return redirect(back_to())


@app.post("/entries/<int:entry_id>/disregard")
def disregard(entry_id):
    tw().disregard(entry_id)
    flash("Disregarded: that entry has been deleted.")
    return redirect(back_to())


@app.get("/summary")
def summary():
    return render_template("summary.html", s=tw().weekly_summary())


if __name__ == "__main__":
    app.run(debug=True)

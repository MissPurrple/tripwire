# Tripwire

**Watch the information you care about, and keep a copy when it changes.**

Pick up to three themes (starting with *Indigenous communities*). Add the Wikipedia articles and web pages that matter to each one. Tripwire checks them for changes. When something changes, it doesn't ping you. Instead it:

- saves the **before** and **after** text into your own archive,
- asks the **Internet Archive** to keep a public copy too,
- tells you how big the change was (punctuation only, a tiny spelling fix, an edit, a major change, a large removal, or the page disappearing),
- **flags names whose spelling changed**, because that can be a sign of a language spreading or fading.

You look through your archive whenever suits you. **Keep** what matters. **Disregard** what doesn't, and it's deleted. A **weekly summary** tells you the big picture, e.g. *"3 changes across 2 of your 8 tracked items. 2 tiny, 1 major."*

Built at the Edge City Buildathon, October 2026.

---

## Running it on your laptop (step by step)

You need **Python 3.10 or newer**. To check, open a terminal and type `python3 --version`. On Windows, use **PowerShell** and type `python --version`. If it isn't installed, download it from https://www.python.org/downloads/. On Windows, tick **"Add Python to PATH"** during installation.

**1. Get the code.** If you have git:

```
git clone https://github.com/MissPurrple/tripwire.git
cd tripwire
```

Without git: on the GitHub page, click the green **Code** button → **Download ZIP**, unzip it, and open a terminal inside the unzipped folder.

**2. Set up a private space for Tripwire's libraries** (a "virtual environment"). You only do this once.

Mac / Linux:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):
```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**3. Start it.**
```
python app.py
```
Then open **http://localhost:5000** in your browser and click **Make an account**. (On your laptop the account only exists on your laptop.) To stop it, go back to the terminal and press **Ctrl + C**.

Next time, you only need to open a terminal in the folder, run the `activate` line from step 2, then `python app.py`.

### Trying it out
1. Add the theme **Indigenous communities**.
2. Add a Wikipedia article, e.g. `Adivasi` or `Warli`, and set **Start** to *"Changes in the last 30 days"*. That way you see real changes straight away, instead of waiting for someone to edit it.
3. Press **Check everything now**, then open **Archive**.

You can also paste links to **other-language Wikipedias** (e.g. Konkani: `https://gom.wikipedia.org/wiki/...`) or **any web page**.

### From the terminal
```
python -m tripwire check     # check everything now, then save Internet Archive copies
python -m tripwire summary   # print everyone's weekly summary
python -m unittest -v        # run the tests (no internet needed)
```

---

## How it's built

| File | What it does |
|---|---|
| `app.py` | The web pages you click around in |
| `tripwire/checker.py` | Adds watches, checks for changes, keeps/disregards entries, writes the weekly summary |
| `tripwire/changes.py` | Compares before and after text and judges how big the change is |
| `tripwire/sources.py` | Talks to Wikipedia, ordinary websites, and the Internet Archive |
| `tripwire/db.py` | Storage: a `tripwire.db` file on your laptop, or a Postgres database online |
| `templates/`, `public/static/` | Page layouts and styling |
| `vercel.json` | Tells Vercel to run the daily check |
| `tests/` | Automatic checks |
| (`notes` branch) | Project notes and decisions, kept separate from the code |

## Putting it online with Vercel

Vercel doesn't keep files between visits, so online Tripwire stores everything in a **Postgres database** instead of the `tripwire.db` file.

1. **Import the project:** on vercel.com, choose **Add New → Project**, pick this GitHub repo, and click **Deploy**. Leave the settings as they are. Vercel finds `app.py` by itself. (The first deploy will show an error page until steps 2 and 3 are done. That's expected.)
2. **Add a database:** in the project, open the **Storage** tab, create a **Postgres** database (Neon is the usual choice, free tier), and connect it to the project. This adds a `DATABASE_URL` setting automatically.
3. **Add two secret settings:** go to **Settings → Environment Variables** and add:
   - `TRIPWIRE_SECRET`: any long random text (keeps logins safe)
   - `CRON_SECRET`: a different long random text (so only Vercel's daily timer can start a check)

   An easy way to make random text: a password manager's generator, at least 32 characters.
4. **Redeploy:** in the **Deployments** tab, open the "…" menu on the latest deployment → **Redeploy**.
5. Open your site's link, make an account, and add your theme.

**Daily checking:** `vercel.json` tells Vercel to visit `/cron` once a day (around 08:47 India time). Each run checks the items that have waited longest, then saves Internet Archive copies, stopping after about 50 seconds so it never runs over Vercel's time limit. Anything that doesn't fit is picked up the next day. If your Vercel plan allows longer runs, set `CRON_BUDGET_SECONDS` to a bigger number.

## Next up
- Delivering the weekly summary by message (once a week only).
- **Social video watching:** paste a YouTube or TikTok link and Tripwire notices if the video is removed, made private, marked sensitive or age-restricted, or retitled.
- **"Watch this with Tripwire" browser extension:** one click to add the page you're on. Also short, live watching while your browser is open (e.g. a BBC live news page), with a sound or ping when it updates.
- Offline, preservation-first copies through **[Kiwix](https://kiwix.org/en/)**. Kiwix packages Wikipedia and other knowledge sites into single files ("ZIM" files) that anyone can keep and read with no internet. Tripwire could compare live articles against a Kiwix copy, and keep its own archive in a format Kiwix can open.

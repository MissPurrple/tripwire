# Tripwire

**Watch the information you care about, and keep a copy when it changes.**

Pick up to three themes (starting with *Indigenous communities*). Add the Wikipedia articles and web pages that matter to each one. Tripwire checks them for changes. When something changes, it doesn't ping you. Instead it:

- saves the **before** and **after** text into your own archive,
- offers to keep a public copy with the **Internet Archive** too (only if you say yes, twice),
- tells you how big the change was (punctuation only, a tiny spelling fix, an edit, a major change, a large removal, or the page disappearing),
- **flags names whose spelling changed**, because that can be a sign of a language spreading or fading.

You look through your archive whenever suits you. **Keep** what matters. **Disregard** what doesn't, and it's deleted. A **summary** (daily, weekly or monthly: you choose) tells you the big picture, e.g. *"3 changes across 2 of your 8 tracked items. 2 tiny, 1 major."*

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
Then open **http://localhost:5000** in your browser. You'll see the opening (falling binary and the purple rabbit); click **TRIP THE WIRES** and the Tripwire computer boots up. You can use everything straight away, without an account. To keep what you add after closing the browser, choose **[M] MAKE AN ACCOUNT**. (On your laptop the account only exists on your laptop.) To see the opening again, open **http://localhost:5000/?intro**. To stop it, go back to the terminal and press **Ctrl + C**.

Next time, you only need to open a terminal in the folder, run the `activate` line from step 2, then `python app.py`.

### Trying it out
1. On the computer's screen choose **[1] WATCHING**, then add the theme **Indigenous communities**.
2. Add a Wikipedia article, e.g. `Adivasi` or `Warli`, and set **Start** to *"Changes in the last 30 days"*. That way you see real changes straight away, instead of waiting for someone to edit it.
3. Press **Check everything now**, then open **Changes**.

You can also paste links to **other-language Wikipedias** (e.g. Konkani: `https://gom.wikipedia.org/wiki/...`) or **any web page**.

### From the terminal
```
python -m tripwire check     # check everything now
python -m tripwire summary   # print everyone's weekly summary
python -m unittest -v        # run the tests (no internet needed)
```

---

## How it's built

| File | What it does |
|---|---|
| `app.py` | The web pages you click around in |
| `tripwire/checker.py` | Accounts and guests, adds watches, checks for changes, keeps/disregards entries, writes the summaries |
| `tripwire/changes.py` | Compares before and after text and judges how big the change is |
| `tripwire/sources.py` | Talks to Wikipedia, ordinary websites, YouTube, TikTok and the Internet Archive |
| `tripwire/books.py` | Reads book files (EPUB, PDF, text, Project Gutenberg) for comparing two editions |
| `tripwire/stats.py`, `tripwire/charts.py` | The Stats page's numbers (Cloudflare Radar, Open Library) and the charts |
| `tripwire/mailer.py`, `templates/emails/` | The summary email (daily, weekly or monthly) |
| `tripwire/humancheck.py` | The sign-up page's own "are you human?" check |
| `tripwire/live.py`, `templates/_live.html` | Live watch (top of the Watching page): checks a page every few minutes while it's open |
| `tripwire/shelf.py`, `templates/export/` | My shelf, and "Take my shelf with me": the ZIP and Kiwix (ZIM) downloads |
| `tripwire/stamp.py`, `templates/verify.html` | Stamps of originality: signed proof of where and when a copy was taken, and the public “Check a stamp” page |
| `tripwire/before.py` | The “Test against Kiwix” and “Test against Internet Archive” buttons: compare a page with an older copy |
| `tripwire/i18n.py`, `translations/` | Languages: English for now; checked translations go in `translations/` |
| `templates/_intro.html`, `public/static/intro.js` | The opening: binary rain, the purple rabbit, "Trip the wires" |
| `public/static/audio/` | Purple's welcome recording (`welcome.mp3`), played on "Trip the wires" |
| `public/static/scan-reader.js`, `public/static/ocr/` | Reads scanned PDFs in the visitor's own browser (Tesseract and pdf.js, served by Tripwire itself) |
| `tripwire/db.py` | Storage: a `tripwire.db` file on your laptop, or a Postgres database online |
| `templates/`, `public/static/` | Page layouts, the retro styling, and the fonts (VT323 and IBM Plex, free under the SIL Open Font License, served by Tripwire itself) |
| `vercel.json` | Tells Vercel this is a Flask app, and to run the daily check |
| `tests/` | Automatic checks |
| (`notes` branch) | Project notes and decisions, kept separate from the code |

## Putting it online with Vercel

Vercel doesn't keep files between visits, so online Tripwire stores everything in a **Postgres database** instead of the `tripwire.db` file.

1. **Import the project:** on vercel.com, choose **Add New → Project**, pick this GitHub repo, and click **Deploy**. Leave the settings as they are. Whatever "Framework Preset" Vercel shows, `vercel.json` tells it this is a Flask app. (The first deploy will show an error page until steps 2 and 3 are done. That's expected.)
2. **Add a database:** in the project, open the **Storage** tab, create a **Postgres** database (Neon is the usual choice, free tier), and connect it to the project. This adds a `DATABASE_URL` setting automatically.
3. **Add two secret settings:** go to **Settings → Environment Variables** and add:
   - `TRIPWIRE_SECRET`: any long random text (keeps logins safe)
   - `CRON_SECRET`: a different long random text (so only Vercel's daily timer can start a check)

   An easy way to make random text: a password manager's generator, at least 32 characters.
4. **Redeploy:** in the **Deployments** tab, open the "…" menu on the latest deployment → **Redeploy**.
5. Open your site's link, make an account, and add your theme.

**Stats page, humans vs bots (optional):** the live numbers come from Cloudflare Radar, which needs a free API token:
1. Sign up (free) at **dash.cloudflare.com**.
2. Click your profile icon (top right) → **My Profile** → **API Tokens** → **Create Token** → **Create Custom Token**.
3. Give it a name like `tripwire-radar`. Under **Permissions**, choose **Account** → **Radar** → **Read**. Click **Continue to summary** → **Create Token**, and copy the token. It's only shown once.
4. In Vercel: **Settings → Environment Variables** → add `CLOUDFLARE_API_TOKEN` with the token as its value → **Save** → **Redeploy**.

The books-published numbers need no setup. They come from Open Library and fill in gradually the first time the Stats page is opened (and during the daily run).

**Summary emails (optional):** Tripwire sends email through any email service that offers "SMTP". Add these in **Settings → Environment Variables**, then **Redeploy**:

| Name | What to put |
|---|---|
| `SMTP_HOST` | the service's SMTP address (see below) |
| `SMTP_PORT` | `587` |
| `SMTP_USER` | the login the service gives you |
| `SMTP_PASSWORD` | the password or key the service gives you |
| `MAIL_FROM` | who it's from, e.g. `Tripwire <you@example.com>` |

*With Proton Mail (needs a Proton address on your own custom domain):* in Proton Mail, go to **Settings → All settings → Proton Mail → IMAP/SMTP → SMTP tokens → Generate token**. Name it `Tripwire`, choose your custom-domain address, click **Generate**, enter your Proton password, and copy the token (it's only shown once). Then use `SMTP_HOST` = `smtp.protonmail.ch`, `SMTP_PORT` = `587`, `SMTP_USER` = that address (e.g. `you@yourdomain.org`), `SMTP_PASSWORD` = the token, `MAIL_FROM` = `Tripwire <you@yourdomain.org>`. To stop Tripwire sending, delete the token in Proton.

*Or with Gmail (free, no domain needed):* best with a new Gmail address just for Tripwire. In that Google account, turn on **2-Step Verification** (myaccount.google.com → Security), then open **myaccount.google.com/apppasswords**, name it `Tripwire`, click **Create**, and copy the 16-letter password (only shown once; leave out the spaces). Use `SMTP_HOST` = `smtp.gmail.com`, `SMTP_PORT` = `587`, `SMTP_USER` = the Gmail address, `SMTP_PASSWORD` = the 16-letter app password, `MAIL_FROM` = `Tripwire <that address>`. Gmail sends up to about 500 emails a day. To stop Tripwire sending, delete the app password.

New accounts get a "confirm your email" link first: Tripwire never emails an address its owner hasn't confirmed.

*Or with Resend (resend.com, free tier):* create an API key; use `SMTP_HOST` = `smtp.resend.com`, `SMTP_USER` = `resend`, `SMTP_PASSWORD` = the API key. To email anyone, verify your own domain in Resend and send from it.

Summaries go out during the daily run: daily ones every day, weekly ones on Mondays, monthly ones on the 1st. Each person chooses on the Summary page (daily, weekly, monthly or off), which also has an **Email it to me now** button. Visitors without an account don't get emails. Optional: `SITE_URL` (e.g. `https://tripwire.vercel.app`) sets the address used in email links.

**Languages:** English for now. A language is added once a native speaker has checked it: drafts of Hindi and Sesotho wait on the `notes` branch. See `CONTRIBUTING.md` to help.

**Feedback inbox (optional):** add `ADMIN_EMAILS` with your own account's email. Then **Account → Feedback inbox** shows what testers wrote in the About page's feedback box. Nobody else can see it.

**Visitors without an account (optional setting):** anyone can try Tripwire without signing up. To stop bots flooding the database, at most 300 new guest visits a hour are allowed across the whole site (nobody is tracked to do this); after that, visitors are asked to make an account. Change the number with a `GUESTS_PER_HOUR` setting. Guests' shelves hold 10 MB, and their things are deleted 3 days after their visit.

**Daily checking:** `vercel.json` tells Vercel to visit `/cron` once a day (around 08:47 India time). Each run checks the items that have waited longest, stopping after about 50 seconds so it never runs over Vercel's time limit. Anything that doesn't fit is picked up the next day. If your Vercel plan allows longer runs, set `CRON_BUDGET_SECONDS` to a bigger number.

**Stamps of originality:** no setup needed. Stamps are signed with a key made from `TRIPWIRE_SECRET`, so keep that setting the same: if it changes, older stamps will show as "made by a different Tripwire site". (To keep stamps working even if you change `TRIPWIRE_SECRET`, set `TRIPWIRE_STAMP_SECRET` once and never change it.)

**My shelf and Kiwix:** no setup needed. My shelf keeps people's own copies (up to 60 MB each, in the database). "Take my shelf with me" downloads it as a ZIP (open `index.html` in any browser) or a ZIM file for **[Kiwix](https://kiwix.org/en/)**, the free app that reads whole libraries offline. The “Test against Kiwix” and “Test against Internet Archive” buttons (next to everything you watch) read older Wikipedia articles from library.kiwix.org and older web pages from the Internet Archive, and offer similar ones when there's no exact copy.

## Next up
- **"Watch this with Tripwire" browser extension:** one click to add the page you're on.
- **A Sesotho voice** for read aloud, and Sesotho-trained text reading for scanned pages.

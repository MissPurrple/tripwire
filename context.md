# Context (last updated 2026-10-28)

## The buildathon
- **Edge City Buildathon.** Started Wednesday 28 Oct 2026 (morning).
- **Ends Friday 30 Oct at 18:00.** Purple believes this is India time (IST = GMT+5:30). Still to confirm with the organisers.
- Teams can have up to 5 people. A team doesn't have to be full.
- Purple is allowed to join up to two more teams on top of the first one.
- There's a train ride from Goa to Mumbai (about 8 hours) on the way to DevCon. People might build things during the ride.

## Teams
- **Joined:** Telemon's team, the "Edge Block Train Experience App". Telemon is on the Edge City team.
  - "Block Train" is a **pun**: everyone is heading to DevCon, which is about Ethereum, crypto, and blockchain. **It does NOT mean the project uses blockchain.**
  - The team's short description says the app is for "all things Edge Block Train programming" (roughly). Purple doesn't know what it involves yet and needs to talk to Telemon.
- **Purple's own project: TRIPWIRE (chosen 28 Oct, solo for now):** a platform that watches information for changes. Users pick what to track (for example up to 3 topics or themes, such as Indigenous communities) and get alerted when it changes on Wikipedia, or when pages are removed from the web / Internet Archive.
- **For Telemon's train app (Purple's suggestion, not yet discussed with Telemon):** "the archive": people record short things in their mother tongue (lullabies, proverbs, recipes, sayings). Purple calls it "an archive of feeling". Related to the "Overheard at Edge" habit in the Edge City Telegram group (short quoted lines people overheard).
- **Rejected ideas:** AI-vs-community comparison, printed archive, human time capsule as its own project.

## Things Purple has clarified (don't get these wrong again)
- Purple does **not** want to use blockchain for their own project.
- Purple does **not** want Indigenous knowledge on a permanent public blockchain, and never suggested it.
- The dead chat rooms / bots taking over old forums topic was **not a project idea**. It was something from a dinner conversation that Purple found fascinating and might be a starting point for thinking.
- Purple's messages are often brain dumps of **separate ideas**. Keep them separate. Don't mash them into one plan.
- Purple wants to **brainstorm together** and hear opinions, not have every sentence critiqued.
- Purple is new to building. Explain clearly and be thorough.

## Open questions
- What exactly is Telemon's app? (Ask Telemon.)
- Are there buildathon rules on using AI tools?

## Tripwire: decisions so far
- Name: **Tripwire**. Purple plans to rename the GitHub repo to match.
- First theme: **Indigenous communities**. Users choose their own themes (a cap of about 3).
- Watches Wikipedia articles **and** any web pages the user adds.
- When something changes, Tripwire **saves** it (a local copy plus an Internet Archive copy of the "before" version) as an entry in the user's archive. It does **not** alert on every change.
- Each entry shows the difference and how big it is (trivial vs. massive). The user can **Disregard** an entry, which deletes it, or keep it.
- Name spelling changes matter. Flag them, because they can show a language spreading or disappearing.
- Notifications: **only a weekly summary** (e.g. "2 changes across 5 articles, mostly trivial"). No per-change Telegram pings.
- **Accounts** added (email + password). Each person has their own themes and archive.
- Hosting: **Vercel** (Purple is setting it up), with a Postgres database. A daily Vercel cron job runs the checks.
- Later: **YouTube / TikTok links**: notice when a video is removed, made private, marked sensitive, or retitled.
- Later / pitch "next step": **browser extension** with a "Watch this with Tripwire" button, plus short live watching while the browser is open (e.g. BBC live news) with a sound or ping.
- Wants offline Wikipedia via **Kiwix**: https://kiwix.org/en/ (Purple: "its more preservation forward as well").

## Team
- Purple wants to open the Tripwire team to more people (up to 5 in total). The call for teammates is in `notes/team-call.md`. The PUBLIC buildathon description must stay vague: no details of how Tripwire works.

## Status (end of 28 Oct 2026)
- **LIVE on Vercel** (new project named "tripwire"; Neon Postgres database connected; TRIPWIRE_SECRET and CRON_SECRET set). Purple made an account and tested it with a real Wikipedia article (age verification laws). Changes showed up correctly.
- GitHub repo renamed to **tripwire**. This cloud session can only push via the old address (github.com/MissPurrple/edge-buildathon), which GitHub redirects.
- Code lives on `main`. Notes live ONLY on the `notes` branch (Purple asked to keep them out of main).

## Decisions made 28 Oct (built)
- **Single links:** watch one link without a theme; doesn't count towards the 3 themes.
- **Internet Archive copies are NEVER automatic.**
  - Offered only for major changes, large removals, removed pages. Name spelling changes are only noted, never offered.
  - Asked twice: (1) "Keep a public copy..." button on the change, and (2) a confirmation screen saying it's public and permanent, with an "I understand" tick box.
  - Also a "Keep a public copy..." option on each watched item, for the version as it is right now.
  - The weekly summary lists important changes that don't have a copy yet.
  - Removed pages can't get a new copy; Tripwire links to older Wayback copies instead.
- Tabs: **Watching · Changes · Archive · Weekly summary**. "Archive" = the person's public Internet Archive copies. "Changes" = Tripwire's own record of changes.

## Open / not decided
- Purple wondered about a one-day delay before offering a copy, then agreed the offer should be on the change itself. Not built as a delay. Ask if it comes up again.
- **Books** (on-demand only, never scheduled): compare editions; metadata like page counts over time, author name spellings, summaries, character names. Claude's brainstorm is in the chat; nothing built yet.
- **Songs:** later, not a priority.

## Priority set 28 Oct (evening)
1. Books, idea 2: compare two digital editions word by word (on request only). BUILT (Books tab).
2. Videos page: YouTube and TikTok links (removed, private, restricted, retitled). BUILT (Videos tab). TikTok can't show 'sensitive' labels publicly.
3. Books, idea 1 (edition facts over time via Open Library) only if there's time.

- Not yet tested against the real YouTube/TikTok (blocked from Claude's cloud computer); first real test is on the live site.

## Built 28 Oct (late evening)
- Tripwire uses NO AI and nothing connected to Purple's Claude account: plain rule-based Python on Vercel. Free, fast, same answer every time.
- Books: donut chart of change kinds (spelling, names, grammar & punctuation, formatting, word swaps, content); "most interesting changes"; book facts panel on the side (title, author, year, publisher, pages, words, language, format for both editions + Open Library quick facts). Project Gutenberg licence text is ignored.
- Book comparisons run as background jobs with a waiting page of nerdy jokes; they carry on if the page is closed. Book files are deleted after 7 days (or on request); results stay.
- Stats page: humans vs bots (Cloudflare Radar, needs CLOUDFLARE_API_TOKEN in Vercel, NOT SET YET); books published running totals + peak/lowest years (Open Library); "Your Tripwire" numbers and charts.
- Menu: Watching · Videos · Changes · Books · Stats + "More" (Archive, Weekly summary, log out); "Menu" button on phones.

## Next
- Retro computer look ("just a little bit"), after core features are solid.
- Purple to add the Cloudflare token, then test Stats, Books (with Gutenberg links) and Videos on the live site.

## Built overnight 28-29 Oct
- Purple added the Cloudflare token. Purple also merged a Vercel Web Analytics change into main (keep it).
- Home page ("/") is a retro box computer that boots up; Watching moved to /watching. Retro look site-wide; fonts self-hosted.
- Styled error pages (404 etc.) and loading states.
- Stats: world book estimate (Google 2010 count + 2.2M-4M new titles/yr) in its own chart; Open Library chart separate; note about unrecorded books (e.g. Lesotho). Open Library fetching is gentler and shows progress/problems.
- Rubric suggestions: see rubric-suggestions.md (Purple to decide which to act on).

## Built 29 Oct (morning)
- Typewriter text on all green screens; human check at sign-up (own, no external service); "HUMAN AFTER ALL" once after sign-up; Books intro "YOU ARE NOW ENTERING UNRESTRICTED KNOWLEDGE PRESERVATION" (min 2 s); Purple's white rabbit signature fixed bottom-right on every page (public/static/rabbit.png, made from Purple's own drawing).
- Purple's design references: green-on-black CRT text (Daft Punk "Human After All", "restricted data files", a terminal with a blinking cursor).

## Built 29 Oct (Purple asleep, second night)
- About page, feedback box + owner-only inbox (ADMIN_EMAILS), demo data button, weekly summary email (SMTP settings). See rubric-suggestions.md and demo-script.md.
- Correction from Purple: testers can't be on the train (the buildathon ends before the train). Purple will find testers in the hours before the deadline.
- Purple's to-do: set SMTP settings + ADMIN_EMAILS in Vercel; press the demo button on the pitch account; share About page with testers.

## Built 29 Oct (Purple awake)
- All green (no orange); chart palette re-chosen without orange/yellow and re-checked for colourblind safety; purple rabbit (white kept as rabbit-white.png).
- Email: Proton Mail SMTP token instructions (Purple won't use Gmail).
- Live watch (More menu): a page checked every 1-5 min while open; beeps; ignores "x minutes ago" noise.
- Scanned PDFs: read in the visitor's browser (Tesseract + pdf.js served by Tripwire); only text uploaded. Tested with two scanned "gazettes": 4 pages read in 6.5 s, changes found.
- Watching PDF links: text PDFs compared by words; scanned PDFs by fingerprint (replaced/removed noticed).
- Brainstorm saved: archive-and-kiwix-ideas.md ("My shelf" + Kiwix ZIM export). Not built yet; Purple to choose.
- Purple will edit some wording later ("too AI-facing").

## Built 29 Oct (afternoon): access and languages
- Access button (bottom-left of every page): read aloud, calm mode (no typing or flashing), bigger text, strong contrast. Saved per browser.
- First visit asks: "Would you like Tripwire to read things aloud to you?" Screen readers get the full text even while it types.
- Read aloud uses Purple's cloned ElevenLabs voice once ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID are set in Vercel (audio cached; per-visitor and daily caps); otherwise the device's own voice.
- Language choice: English, draft Hindi, draft Sesotho (footer and Access panel). Choosing one shows Purple's "still in building form" notice. Drafts written by Claude, not yet checked by native speakers. Hindi audio: work in progress.
- CONTRIBUTING.md explains how to translate. Open questions for Purple: which open-source licence, and making the repo public.

## Built 29 Oct (evening): My shelf, Kiwix, Then and now
- ElevenLabs removed (Purple, a voice artist, decided not to clone their voice for now). Read aloud = device voice only. Old code is in git history (commit 067779c) if ever wanted.
- My shelf (main menu): keep files (scans/photos read on the device), PDF links, web pages, Wikipedia articles; search; optional watching of the original. 60 MB per person.
- Take my shelf with me: ZIP (index.html works offline in any browser) and ZIM for Kiwix (searchable). Checked with the real kiwix-serve: opens, search works, PDFs open.
- Then and now (More menu, and a button on every watched item): Wikipedia vs Kiwix's newest full offline copy (library.kiwix.org); other pages vs Internet Archive copy from ~1 year ago. NOT yet tested against the real Kiwix/Archive servers (blocked from the build machine): Purple to try on the live site.
- Fixed: the scan progress box on Books was always visible.
- Email: Proton needs a paid plan + custom domain. Advice given: skip email for the buildathon (the Weekly summary page works without it). Purple's open question: Gmail and an Indian phone number.
- Future (pitch): a Sesotho voice and Sesotho-trained scan reading. Meta's open MMS voices likely include Sesotho (not confirmed; licence to check).
- Ask Purple about going public (licence + public repo) AFTER these steps, as Purple asked.

## Built 29 Oct (opening, no forced sign-up, summary choice)
- **Opening ("front door")** on the home page, once per visit (`/?intro` replays it): binary rain; TRIPWIRE typed;
  REMEMBER EVERYTHING. in green (green on black in dark mode, green on cream in light mode); the purple rabbit hops in
  from the right (it faces left) Pixar-lamp style (crouch, stretch, land, squash, a head tilt "looking at you"), ends
  centred in the logo pose; then **[ TRIP THE WIRES ]** + "Follow the purple rabbit." Clicking it turns the screen
  black, binary rains across all of it, then the box computer boots. Skip button and Escape; still version for
  people who turn animations off. Files: templates/_intro.html, public/static/intro.js.
- **Purple's voice:** browsers block sound until the visitor clicks, so it plays on "Trip the wires" (Purple chose
  this). Recording goes in public/static/audio/welcome.mp3 (not recorded yet; until then the opening is silent, plus
  a small synth chirp on click).
- **No forced sign-up (Purple chose "try everything"):** visitors use everything without an account. The first time
  they save something, Tripwire starts a "guest" visit (lasts until the browser closes; deleted after 3 days by the
  daily run). A one-line note invites them to make an account to keep things. Signing up keeps everything; logging
  in moves a guest's things into the account. Still need an account: public Internet Archive copies, emails.
  Guests' items aren't in the daily check (they press Check). Feedback works without an account (hidden bot trap).
- **Summary emails: daily, weekly (Mondays) or monthly (the 1st), or off.** Older accounts keep their old on/off.
- About: "How Tripwire changed" kept (Purple was unsure; Claude recommended keeping it: iteration is 15% of the
  rubric, and it invites help). Added a "help is very welcome" line and the three changes above to the timeline.
- Open: Purple to record the welcome line; Purple to send a bigger version of the rabbit drawing (the current file is
  76x120 pixels, a little soft when enlarged in the opening).
- Status: LIVE (merged into main 29 Oct, Purple asked to go live; tagline changed to ARCHIVE & PRESERVE.), merged with the parallel session's My shelf,
  Then and now, access mode and languages. My shelf and Then and now also work without an account. The read-aloud
  question now appears after the opening. 261 tests pass (SQLite and Postgres).
- OPEN: Purple saw 500 errors on the preview (database settings ARE enabled for Preview). A simulated-Vercel run showed
  no errors on any page. Need the Vercel Logs error text to find the cause.
- Wording (29 Oct, from Purple, live): home page and About intro + quote rewritten in Purple's words. Keep "Indigenous"
  with a capital I. Keep "No account needed." under About → Deliberate choices (important for the judges to see).

## Security and privacy audit (29 Oct, cypherpunk lens; Purple: reading is fine, breaking things is not)
- FIXED and live: links only http(s) and never into private networks (every redirect re-checked), 50 MB download
  cap, passwords not passed between sites; safe "next" redirects; browser protection headers; guest flood limit
  (GUESTS_PER_HOUR, default 300, site-wide, no tracking) and 10 MB guest shelf; "Your account" page with
  delete-everything (password + tick box); honest warning about very personal documents (shelf, books, about, account).
- SUGGESTED, not built (Purple to choose): slow down repeated wrong passwords; confirm email addresses before
  sending summaries (a bot could sign up a stranger's email); form tokens against cross-site form tricks
  (browsers' SameSite cookies already block most); keep ADMIN_EMAILS to Purple's own registered email; consider
  removing the Vercel Analytics script (cookieless, but a third party sees visits); end-to-end encrypted shelf (big job).
- BUILT and live (29 Oct): suggestions 1-3. Log-in pauses 15 min after 5 wrong passwords; email addresses must be
  confirmed (link → page with a button) before any summary is sent; forms from other websites are refused.
- Email: Purple now chooses **Gmail for now** (Proton needs a custom domain Purple doesn't have). Steps are in the README
  (new Gmail address + 2-Step Verification + app password). ADMIN_EMAILS = the address Purple logs in to Tripwire with.
- Next to discuss: suggestion 6 (shelf encrypted with a key only the person holds).

## Built 29 Oct (after Claude's rubric review, scored 3.3/5): five pages
- Review notes: biggest gaps were no real testers yet (Purple is finding some) and too many pages. Purple's decisions:
  KEEP the slow opening with NO skip (still version only for reduced-motion users), KEEP the retro look, KEEP About long
  and detailed for the judges (Purple will read it and decide what to cut; don't shorten it without asking).
- "Indigenous" appears in: About (Who it's for + Purple's own quote), the example theme name, the demo examples. It's a
  theme people choose; Purple is fine with it.
- Menu: Watching · My shelf · Changes · Books & videos · About (+ Account menu). Live watch = switch at the top of Watching
  (off by default). Watched videos listed on Watching. Books & videos side by side; "Check it now" or "Watch it" for videos;
  "Keep watching the links" for books (links only; EPUB/text/web). Archive (public copies) inside My shelf. Summary at the top
  of Changes. Stats link at the bottom of every page. Old addresses redirect.
- "Then and now" replaced by two buttons on every watched item: Test against Kiwix (Wikipedia only) / Test against Internet
  Archive. No exact copy -> similar articles (Kiwix title search) or nearby saved pages (Internet Archive). Still not tested
  against the real Kiwix/Archive servers (blocked from Claude's machine): Purple to try on the live site.
- Hindi/Sesotho drafts OFF the site; saved here in translation-drafts/. About: "What we'd like to see next" invites translators.
- About additions (Purple's points): "Why not just use what already exists?" and "communities not fully online".
- Fixes: empty first visit shows "Try it with real examples"; daily checks ignore "updated 5 minutes ago" noise; long pages
  compared sentence by sentence (fast). 312 tests pass on SQLite and Postgres.
- The other Claude session works in parallel on security (log-in pauses, email confirmation, cross-site form refusal).

## Built 29 Oct: Your vault (suggestion 6), on branch `locked-shelf` ONLY (not live, not on main)
- Purple: "build it with the full ethos of being a true cypherpunk site... the ultimate archive tool". Separate branch so
  nothing breaks. Vercel makes a preview for it.
- Shelf items locked in the browser (AES-256-GCM; passphrase -> PBKDF2-SHA256 600k; recovery code shown once).
  Server stores only locked data; tested: no title/note/words/file name/passphrase/recovery code in the raw database.
- Offline unlock page in the ZIP download. Locked items: not searchable/watchable/in Kiwix (server can't read them).
- Honest limit (on the vault page): web-page encryption trusts the page the server sends.
- Possible next: lock "Then and now" results and feedback (feedback locked to Purple's own key) the same way.
- Gmail: Purple got "verification failed" making an app password on a brand-new Gmail account (Google often blocks
  this on new accounts for a while). Paused.
- 30 Oct: the vault is LIVE (merged into main). Added Purple's dramatic warning ("lose your passphrase and you're
  locked out... what you've vaulted will disintegrate"; Purple said "voted", read as "vaulted") on the vault setup,
  the recovery code step, unlocking, and the shelf.

## Built 30 Oct (morning)
- Kiwix test on Basotho compared against ~59 words of what looks like a "prove you're not a bot" page (mentions AI crawlers,
  JavaScript). Not confirmed (Claude can't reach library.kiwix.org). Fixed: read Kiwix copies from the raw address
  (/raw/<zim>/content/<path>); page title must match the article; robot-check pages and suspiciously short copies refused;
  results show the start of both versions + where/when each was fetched; if Kiwix blocks, offer "Wikipedia's own version from
  the date of Kiwix's copy". Checked against the real kiwix-serve software. Purple to retry on the live site and delete the
  old bad Basotho comparison (Delete button at the bottom of it).
- Stamps of originality on every new shelf copy (signed, Ed25519, key from TRIPWIRE_SECRET or TRIPWIRE_STAMP_SECRET):
  fetched-by-Tripwire vs uploaded-by-owner, time, link, Wikipedia permanent version link, fingerprints. Public /verify page.
  In ZIP/ZIM downloads. Not for vault (locked) items yet: they'd need stamping in the browser.
- Bug found and fixed: Books with two links (and shelf uploads of plain files) never sent since 29 Oct.
- Test results no longer listed under watched items.
- Examples: two articles per theme (Purple: two per section, not five); second book: Darwin's Origin of Species 1859 (#1228)
  vs 1872 (#2009). Most famous recent case (Roald Dahl, 2023) is under copyright: no free copies to compare.

## Built 30 Oct: first-time tour
- Four steps (Watching, My shelf, Changes, Books & videos), after the opening + read-aloud question, once per device
  (localStorage "tripwire-tour"). Skip button / Escape. "Take the tour" in the footer replays it. Last step: "Try it with
  real examples" or "I'll explore on my own". Phones: points at Menu. Files: templates/_tour.html, public/static/tour.js.
- Clean-up suggestions given to Purple (not built, waiting for Purple's choice): shelf items first with add forms folded
  away; one add form on Watching instead of one per theme; fewer buttons per watched item (a "More" menu); Changes filters
  as a dropdown; shorter notes on Books & videos.

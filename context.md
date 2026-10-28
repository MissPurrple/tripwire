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

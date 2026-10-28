# Rubric suggestions (written 28-29 Oct 2026, while Purple slept)

The rubric, scored 0-5 per criterion:
- Meaningful value for intended people: 30%
- Originality and purposeful craft: 25%
- Tangible result and evidence of delivery: 30%
- Thoughtful iteration: 15%

Purple's brief: suggestions only; don't change everything to fit the rubric, but aim high.

## 1. Meaningful value (30%): say who it's for, and show it helping them
1. Name the people, everywhere (pitch, home page, an About page): Indigenous communities and the people who document them; independent journalists, storytellers and archivists; anyone whose subject is thin online and easy to rewrite.
2. Get 2-3 real people to try it before the deadline (NOT on the train: the buildathon ends before the train, and Telemon's train app is meant to be ready FOR the train) and collect a one-line quote from each. They can use the feedback box on the About page. Best: someone from a minority-language community, an archivist or librarian, a journalist. Judges weigh real people over imagined ones.
3. Show a real catch, not a pretend one: a real edit on a real Indigenous-community Wikipedia article that Tripwire found (90-day look-back), and a real book comparison with real differences.
4. Name the harm it prevents in one sentence: "When the record of a community is quietly changed, nobody notices. Tripwire notices, and keeps a copy."

## 2. Originality and purposeful craft (25%): explain WHY each choice was made
The rubric credits choices that improve the result, not complexity. Tripwire has many deliberate ones; say them out loud:
1. No AI inside, on purpose: same answer every time, free, can't make things up. That fits a tool about keeping the record honest.
2. Public copies are asked twice, with a warning about sacred or private community knowledge (in the spirit of the CARE principles for Indigenous data).
3. A weekly summary, not pings: built to avoid alarm fatigue.
4. Name-spelling changes are flagged, because a respelled name can mean a language spreading or fading.
5. Book files are deleted after 7 days; fonts are served by Tripwire itself; nothing is sold or tracked.
6. Honest numbers: the stats page says what it can't count (books no one recorded, e.g. in Lesotho).
7. The retro computer is a metaphor ("the record of the world"), not decoration. Say that.
8. Be open that Claude helped build it: the rubric explicitly allows AI-assisted choices, judged by whether they improve the result.

## 3. Tangible result (30%): make the demo impossible to doubt
1. A demo account with real data, set up before the pitch: real watched pages and changes, a book comparison, a public copy in Archive, a video.
2. Record a 2-3 minute screen video as a backup (train or venue Wi-Fi can fail).
3. Evidence slide: live link; how many people signed up; changes caught; 154 automatic tests that pass on two kinds of database.
4. Keep demonstrated vs future separate. Don't present as working: the weekly summary message (today it's a page, not a message), the browser extension, Kiwix. Put them under "Next".
5. Be accurate about limits: TikTok doesn't publicly show "sensitive" labels; YouTube sometimes blocks servers.

## 4. Thoughtful iteration (15%): show how it changed, and why
The notes branch already records this in Purple's own words. Turn it into a short "How Tripwire changed" timeline:
- Alerts for every change -> one weekly summary (avoid alarm fatigue)
- Automatic Internet Archive copies -> asked twice, only for big changes (public and permanent)
- Themes only -> single links too (care about one page, not a whole topic)
- Every change in one long list -> change-type pie chart, highlights, "show all"
- Gutenberg licence text showing as fake changes -> ignored
- Open Library only -> plus a world estimate, because Open Library leans Western
- Deployment problems fixed (Vercel 404, database), plus a Postgres bug caught by tests before launch
- Plus 1-2 changes made from real tester feedback (before the deadline): the strongest evidence of all

## Things Claude offered to build (Purple to choose)
- An "About Tripwire" page: who it's for, why, the values, and the iteration timeline above
- A demo-data setup for the pitch account
- The weekly summary actually sent (email), so it's demonstrated rather than promised

## Done (29 Oct, while Purple slept)
- About page: who it's for, why (Purple's own words), what it does, deliberate choices, honest limits, "How Tripwire changed" timeline, live totals, credits.
- Feedback box on About + an owner-only Feedback inbox (needs ADMIN_EMAILS in Vercel).
- Demo data button on About ("Add the examples to my account").
- Weekly summary by email (needs SMTP settings in Vercel; see README).
- See demo-script.md for a pitch walkthrough.

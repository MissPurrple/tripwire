# Helping with Tripwire

Tripwire is made by one person from one community. Nobody can know, alone, what every
community needs to preserve. So it's built to be adapted: please translate it, fix it,
and change it to fit your people.

## Translate Tripwire into your language

Each language is one simple file in the `translations/` folder, and it appears on the site
(with a language choice at the bottom of every page) once it's there. Tripwire is in English
for now: a language is only added once a native speaker has checked it.

First drafts of **Hindi** and **Sesotho** are waiting on the `notes` branch, in
`translation-drafts/`. If you speak one of them, checking a draft is the most helpful thing
you can do.

Each line pairs the English words with your language's words:

```json
"Books": "Libuka",
```

To add a language, start from one of the drafts (or copy its layout) into a new file named
with the language's code (for example `hi.json`, `st.json`, `zu.json` for isiZulu or `sw.json`
for Kiswahili), set `_language` to the language's own name, and translate each line. Anything
you leave out simply stays in English, so a partial translation is fine and helpful.

The Hindi and Sesotho drafts were written with the help of an AI and haven't been checked by
native speakers yet, which is why they aren't on the site.

To send your changes: on GitHub, open the file, click the pencil icon ("Edit"), make your
changes, and choose "Propose changes". That creates a pull request for review. If GitHub
isn't your thing, leave a message in the feedback box on Tripwire's About page instead.

## Voices

Tripwire can read every page aloud, using the device's own voice. Many devices have no
Sesotho voice yet; if you know of a good, freely licensed one, please get in touch.

## Code

Tripwire is a small Python (Flask) app. See the README for how to run it on your laptop and
how it's organised. Before sending a change, run the tests:

```
python -m unittest -v
```

## Values to keep

- Ask before making anything public, and remember that some knowledge is not meant to be public.
- No AI inside Tripwire itself: plain, checkable rules.
- Privacy first: no ads, no selling data, nothing loaded from services that track visitors.
- Accessible to people who can't see, can't read, or don't read English.

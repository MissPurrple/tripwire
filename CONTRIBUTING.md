# Helping with Tripwire

Tripwire is made by one person from one community. Nobody can know, alone, what every
community needs to preserve. So it's built to be adapted: please translate it, fix it,
and change it to fit your people.

## Translate Tripwire into your language

Each language is one simple file in the `translations/` folder:

- `translations/hi.json`: Hindi (draft)
- `translations/st.json`: Sesotho (draft)

Each line pairs the English words with your language's words:

```json
"Books": "Libuka",
```

To improve a language, edit its file. To add a new language, copy `st.json` to a new file
named with the language's code (for example `zu.json` for isiZulu or `sw.json` for Kiswahili),
change `_language` to the language's own name, and translate each line. Anything you leave
out simply stays in English, so a partial translation is fine and helpful.

The current Hindi and Sesotho files are drafts written with the help of an AI and not yet
checked by native speakers. Corrections from native speakers are especially welcome.

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

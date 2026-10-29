"""Languages. English is built in; other languages live in translations/<code>.json,
one simple file each, so anyone can add or improve a language (see CONTRIBUTING.md).
Anything not translated yet appears in English."""

import json
import os

FOLDER = os.path.join(os.path.dirname(__file__), "..", "translations")
DEFAULT = "en"


def _load():
    languages = {"en": {"_language": "English", "_english_name": "English"}}
    for name in sorted(os.listdir(FOLDER)) if os.path.isdir(FOLDER) else []:
        if name.endswith(".json"):
            with open(os.path.join(FOLDER, name), encoding="utf-8") as f:
                languages[name[:-5]] = json.load(f)
    return languages


LANGUAGES = _load()


def translate(lang, text):
    return LANGUAGES.get(lang, {}).get(text, text)


def choices():
    """[(code, name in its own language)], English first."""
    return [(code, data["_language"]) for code, data in LANGUAGES.items()]

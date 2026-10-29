"""Reading text aloud in Purple's voice, through ElevenLabs (elevenlabs.io).

Optional: set ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID in Vercel. Without them,
the browser reads with the device's own built-in voice instead (free, and works offline).

To look after the ElevenLabs credits:
  - every piece of audio is remembered, so the same text is never paid for twice
  - each visitor can only have so much read per 10 minutes (see app.py)
  - the whole site stops using ElevenLabs for the day after ELEVENLABS_DAILY_CHARACTERS
    (the device voice takes over until tomorrow)
"""

import hashlib
import os
import time

import requests

from . import db

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_64"
DEFAULT_MODEL = "eleven_multilingual_v2"   # one voice that can also speak Hindi, Sesotho text and more
MAX_CHARACTERS = 800                       # one request: a paragraph or two
KEEP_CACHED = 300                          # remembered pieces of audio


class SpeechError(Exception):
    """Couldn't make the audio. The browser falls back to the device voice."""


def configured():
    return bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_VOICE_ID"))


def daily_limit():
    return int(os.environ.get("ELEVENLABS_DAILY_CHARACTERS", "30000"))


class Speech:
    def __init__(self, conn):
        self.conn = conn
        blob = "BYTEA" if conn.postgres else "BLOB"
        conn.execute(f"CREATE TABLE IF NOT EXISTS speech_cache (key TEXT PRIMARY KEY, audio {blob} NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS speech_usage (day TEXT PRIMARY KEY, characters INTEGER NOT NULL)")

    @staticmethod
    def key(text, lang):
        voice = os.environ.get("ELEVENLABS_VOICE_ID", "")
        return hashlib.sha256(f"{voice}|{lang}|{text}".encode()).hexdigest()

    def used_today(self):
        row = self.conn.execute("SELECT characters FROM speech_usage WHERE day = ?", (time.strftime("%Y-%m-%d"),)).fetchone()
        return row["characters"] if row else 0

    def audio(self, text, lang="en"):
        """MP3 audio of `text` in Purple's voice: from memory if read before, otherwise from ElevenLabs."""
        text = " ".join((text or "").split())[:MAX_CHARACTERS]
        if not text:
            raise SpeechError("Nothing to read.")
        key = self.key(text, lang)
        row = self.conn.execute("SELECT audio FROM speech_cache WHERE key = ?", (key,)).fetchone()
        if row:
            return bytes(row["audio"])
        if not configured():
            raise SpeechError("Purple's voice isn't connected on this site.")
        if self.used_today() + len(text) > daily_limit():
            raise SpeechError("Today's reading allowance is used up; your device's voice will read instead.")
        try:
            response = requests.post(API.format(voice=os.environ["ELEVENLABS_VOICE_ID"]), timeout=45,
                                     headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
                                     json={"text": text, "model_id": os.environ.get("ELEVENLABS_MODEL", DEFAULT_MODEL)})
        except requests.RequestException as exc:
            raise SpeechError("Couldn't reach ElevenLabs.") from exc
        if response.status_code in (401, 403):
            raise SpeechError("ElevenLabs didn't accept the API key.")
        if response.status_code != 200 or not response.content:
            raise SpeechError(f"ElevenLabs answered with error {response.status_code}.")
        audio = response.content
        self.conn.execute("""INSERT INTO speech_cache (key, audio, created_at) VALUES (?, ?, ?)
                             ON CONFLICT (key) DO NOTHING""", (key, audio, db.now()))
        self.conn.execute("""INSERT INTO speech_usage (day, characters) VALUES (?, ?)
                             ON CONFLICT (day) DO UPDATE SET characters = speech_usage.characters + excluded.characters""",
                          (time.strftime("%Y-%m-%d"), len(text)))
        self.conn.execute("""DELETE FROM speech_cache WHERE key NOT IN
                             (SELECT key FROM speech_cache ORDER BY created_at DESC LIMIT ?)""", (KEEP_CACHED,))
        return audio

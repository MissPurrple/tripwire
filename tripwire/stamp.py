"""Stamps of originality: a signed note that says what a copy is, where it came from,
when it was taken, and how (fetched by Tripwire itself, or uploaded by its owner).

Each stamp holds a fingerprint (SHA-256) of the copy's words and of its file: change a
single letter and the fingerprint no longer matches. The stamp is signed with Tripwire's
own key (Ed25519), so nobody can make or edit a stamp without that key. Anyone can check
one on the site's Verify page, or offline with the public key written in the stamp.

What a stamp proves, and what it can't: that this Tripwire took (or was given) this exact
copy at this time, and that it hasn't changed since. Someone who doesn't trust Tripwire
would want a second witness too: for Wikipedia, the link to the exact version (in the
stamp, and anyone can open it); for other pages, a public Internet Archive copy.
"""

import base64
import hashlib
import json
import os
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from . import db

KIND = "Tripwire copy stamp"
VERSION = 1


def fingerprint(data):
    """SHA-256 of bytes or text, as hex."""
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(stamp):
    """The exact bytes that are signed: every field but the signature, in a fixed order."""
    return json.dumps({k: v for k, v in stamp.items() if k != "signature"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _seed(conn):
    """The 32 bytes Tripwire's signing key is made from. Online, from the site's secret setting;
    on a laptop without one, a random key made once and kept in the database."""
    secret = os.environ.get("TRIPWIRE_STAMP_SECRET") or os.environ.get("TRIPWIRE_SECRET")
    if secret:
        return hashlib.sha256(b"tripwire-stamp-v1:" + secret.encode("utf-8")).digest()
    row = conn.execute("SELECT value FROM site_keys WHERE name = 'stamp'").fetchone()
    if not row:
        conn.execute("INSERT INTO site_keys (name, value) VALUES ('stamp', ?)", (secrets.token_hex(32),))
        row = conn.execute("SELECT value FROM site_keys WHERE name = 'stamp'").fetchone()
    return bytes.fromhex(row["value"])


class Stamper:
    def __init__(self, conn):
        self.key = Ed25519PrivateKey.from_private_bytes(_seed(conn))
        raw = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_key = _b64(raw)
        self.key_id = fingerprint(raw)[:16]

    def make(self, **fields):
        """A signed stamp holding `fields` (title, how, source_url, taken_at, fingerprints...)."""
        stamp = {"kind": KIND, "version": VERSION, **fields, "public_key": self.public_key, "key_id": self.key_id}
        stamp["signature"] = _b64(self.key.sign(canonical(stamp)))
        return stamp

    def for_copy(self, *, title, how, text, data=None, file_name=None, source_url=None,
                 text_from=None, wikipedia_version=None, taken_at=None):
        """The stamp for one copy kept on a shelf. how: "fetched" (Tripwire fetched it itself) or "uploaded"."""
        return self.make(title=title, how=how, source_url=source_url, taken_at=taken_at or db.now(),
                         text_sha256=fingerprint(text) if text else None, text_from=text_from,
                         file_name=file_name if data else None, file_sha256=fingerprint(data) if data else None,
                         wikipedia_version=wikipedia_version)

    def check(self, stamp):
        """Is this a genuine stamp? Returns (verdict, message): verdict is "genuine" (made by this
        Tripwire and unchanged), "other_site" (genuine, but made with another Tripwire's key) or "fake"."""
        try:
            if stamp.get("kind") != KIND:
                return "fake", "This isn't a Tripwire stamp."
            Ed25519PublicKey.from_public_bytes(_unb64(stamp["public_key"])).verify(
                _unb64(stamp["signature"]), canonical(stamp))
        except (InvalidSignature, KeyError, ValueError, TypeError, AttributeError):
            return "fake", ("The signature doesn't match: this stamp has been changed since it was made, "
                            "or it wasn't made by Tripwire.")
        if stamp["public_key"] != self.public_key:
            return "other_site", ("The signature is genuine, but it was made by a different Tripwire site "
                                  f"(key {stamp.get('key_id')}), not this one.")
        return "genuine", "Genuine: made by this Tripwire, and not changed by a single letter since."


def matches(stamp, data):
    """Which part of a stamped copy `data` (the file, or the words as plain text) is, if any."""
    found = fingerprint(data)
    if stamp.get("file_sha256") and found == stamp["file_sha256"]:
        return "file"
    if stamp.get("text_sha256"):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if found == stamp["text_sha256"] or (text is not None and fingerprint(text.replace("\r\n", "\n")) == stamp["text_sha256"]):
            return "text"
    return None

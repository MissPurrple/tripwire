"""Tripwire's own "are you human?" check, used once, when making an account.
No outside service is involved. It layers four simple hurdles:

  1. A question in words ("What is seven plus five?"). Easy for people, and a
     program has to be written specially to read it.
  2. A small proof of work: the browser quietly tries numbers until a hash
     starts with enough zero bits (about a second of work). Trivial for one
     person, costly for a bot making thousands of accounts.
  3. A hidden "website" field people never see; bots that fill every field do.
  4. A minimum time on the page; bots fill forms in milliseconds.

No check stops a determined AI. This makes automated sign-ups slow and costly.
"""

import hashlib
import hmac
import random
import secrets
import time

NUMBER_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
                "nineteen", "twenty"]
LETTER_WORDS = ["archive", "library", "record", "memory", "story", "elder", "river", "language", "village", "song"]

CHALLENGE_MINUTES = 15     # a question expires after this long
DEFAULT_BITS = 14          # proof of work: about 16,000 tries on average
DEFAULT_MIN_SECONDS = 3    # nobody reads a question and types an answer faster than this


def _question():
    kind = random.choice(["plus", "minus", "letters"])
    if kind == "plus":
        a, b = random.randint(2, 9), random.randint(2, 9)
        return f"What is {NUMBER_WORDS[a]} plus {NUMBER_WORDS[b]}?", a + b
    if kind == "minus":
        a, b = random.randint(6, 15), random.randint(1, 5)
        return f"What is {NUMBER_WORDS[a]} minus {NUMBER_WORDS[b]}?", a - b
    word = random.choice(LETTER_WORDS)
    return f"How many letters are in the word “{word.upper()}”?", len(word)


def _sign(secret, value):
    key = secret if isinstance(secret, bytes) else str(secret).encode()
    return hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()


def new_challenge(secret, bits=DEFAULT_BITS):
    """A fresh challenge to keep in the session. The answer is stored only as a
    signature, because Flask's session can be read (though not changed) by the visitor."""
    question, answer = _question()
    return {"question": question, "answer": _sign(secret, answer), "salt": secrets.token_hex(8),
            "bits": bits, "issued": time.time()}


def _number(text):
    text = (text or "").strip().lower().rstrip(".")
    if text.isdigit():
        return int(text)
    return NUMBER_WORDS.index(text) if text in NUMBER_WORDS else None


def pow_ok(salt, nonce, bits):
    digest = hashlib.sha256(f"{salt}:{nonce}".encode()).digest()
    value = int.from_bytes(digest, "big")
    return value >> (256 - bits) == 0


def check(challenge, secret, answer, nonce, honeypot="", min_seconds=DEFAULT_MIN_SECONDS, now=None):
    """Returns (passed, message). The message is shown on the check's little screen."""
    now = now or time.time()
    if not challenge:
        return False, "The check expired. Here's a new question."
    if honeypot:
        return False, "That didn't look like a person. Try again."
    if now - challenge["issued"] > CHALLENGE_MINUTES * 60:
        return False, "That question expired. Here's a new one."
    if now - challenge["issued"] < min_seconds:
        return False, "That was quicker than a human can type. Take a breath and try again."
    if not pow_ok(challenge["salt"], nonce, challenge["bits"]):
        return False, "The background check didn't finish. Try again."
    number = _number(answer)
    if number is None or not hmac.compare_digest(_sign(secret, number), challenge["answer"]):
        return False, "Not quite. Here's a new question."
    return True, "DONE."

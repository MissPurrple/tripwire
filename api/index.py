"""Vercel's entry point. Vercel runs every file in api/ as a program; this one
just loads Tripwire's website from app.py. vercel.json sends every visit here."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401

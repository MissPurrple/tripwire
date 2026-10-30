"""Reading a page the way a browser does.

Most pages arrive with their words in them. Some (like the Artizen Playbook) arrive almost
empty and fill themselves in with JavaScript after they open, so a plain download finds
nothing to keep. For those, Tripwire opens the page in a real (headless) browser and keeps
the words a person would see:

  - Online (Vercel): Tripwire's own small browser function, api/render.js, on the same site.
    Only Tripwire can call it (a key made from TRIPWIRE_SECRET).
  - On a laptop: a browser installed with Python's Playwright, if there is one
    (pip install playwright, then: playwright install chromium).
  - TRIPWIRE_RENDER_URL can point at any copy of api/render.js instead.

The browser is only used when a page needs it (see looks_unfinished), and the choice is
remembered for each watched page, so a page never flips between the two ways of reading
and shows a "change" that didn't happen.
"""

import hashlib
import os
import re

import requests

from . import sources

TIMEOUT = 45
# Signs that a page fills itself in after it opens.
APP_SHELL = re.compile(r'<div id="(?:root|app|__next|__nuxt|svelte)"[^>]*>\s*</div>|'
                       r"(?:enable|requires?|need to enable) javascript|javascript is (?:disabled|required)", re.I)
FEW_WORDS = 120


class RenderError(sources.SourceError):
    """The page couldn't be opened in a browser."""


def key(secret):
    return hashlib.sha256(("tripwire-render-v1:" + secret).encode("utf-8")).hexdigest()


def endpoint():
    """Where the browser function lives, or None (then a local browser is tried)."""
    if os.environ.get("TRIPWIRE_RENDER_URL"):
        return os.environ["TRIPWIRE_RENDER_URL"]
    if os.environ.get("VERCEL"):
        # The production address: preview addresses sit behind Vercel's login, which would block the call.
        site = os.environ.get("SITE_URL") or (
            "https://" + os.environ["VERCEL_PROJECT_PRODUCTION_URL"] if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") else None)
        return site.rstrip("/") + "/api/render" if site else None
    return None


def available():
    if endpoint():
        return bool(os.environ.get("TRIPWIRE_SECRET"))
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def looks_unfinished(html, text):
    """Does this page probably fill in its words after it opens?"""
    words = len(text.split())
    return words < FEW_WORDS and (len(html) > 1500 or bool(APP_SHELL.search(html))) or bool(APP_SHELL.search(html) and words < 400)


def render(url):
    """The words of `url` as a browser shows them. Returns {"text", "title", "url"}. Raises RenderError."""
    sources._check_public(url)
    where = endpoint()
    if where:
        try:
            response = requests.post(where, json={"url": url}, timeout=TIMEOUT,
                                     headers={"X-Tripwire-Render": key(os.environ.get("TRIPWIRE_SECRET", ""))})
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RenderError("Tripwire's browser couldn't be reached. It will try again next time.") from exc
        if response.status_code != 200:
            raise RenderError(data.get("error") or f"Tripwire's browser answered with error {response.status_code}.")
        return {"text": data.get("text") or "", "title": data.get("title") or "", "url": data.get("url") or url}
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RenderError("This page fills in its words after it opens, and there's no browser here to read it "
                          "(on a laptop: pip install playwright, then playwright install chromium).") from exc
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font")
                           else route.continue_())
                try:
                    page.goto(url, wait_until="networkidle", timeout=15000)
                except PlaywrightError:
                    pass  # still loading: read whatever has arrived
                page.wait_for_timeout(500)
                return {"text": page.inner_text("body"), "title": page.title(), "url": page.url}
            finally:
                browser.close()
    except PlaywrightError as exc:
        raise RenderError("The browser couldn't open that page.") from exc


def better(plain, rendered):
    """Did the browser find clearly more of the page than the plain download?"""
    p, r = len(plain.split()), len(rendered.split())
    return r >= max(p * 1.5, p + 60)

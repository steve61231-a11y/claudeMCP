"""A downloadable file of a finished report — no link, no login, just a file.

The client wants to hand a completed report to someone outside this system —
an outside analyst who is going to internalise it and come back with his own
structural recommendations — without giving that person a URL into a live,
API-key-gated app. A PDF is the artifact for that: it travels by email, by
WhatsApp, whatever, and it is exactly what the page shows, because it IS the
page, printed.

This reuses the app's own rendering code rather than building a second,
parallel PDF template that would drift from what the live page actually
shows. The same HTML document the browser gets, the same `window.ZENITH.*`
render function the browser calls, loaded headlessly and printed.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Where a Chromium might be. This was a single hard-coded path to the
#: DEVELOPER'S container — /opt/pw-browsers/chromium-1194/… — which existed
#: nowhere else, so this module reported "not available" on the deployed
#: service and the download button returned 503 in the only place a user
#: would ever press it. Look in the usual places, and let an operator say.
#:
#: The page no longer depends on any of this: "Download PDF" prints from the
#: reader's own browser, which has already rendered the report and needs no
#: browser installed on the server. These endpoints remain for callers that
#: want the bytes server-side (tests, and any future scheduled export), and
#: they now degrade honestly wherever no browser is installed.
import os
import shutil

_ENV_OVERRIDE = "CHROME_EXECUTABLE_PATH"
_CANDIDATE_PATHS = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)

#: payload "kind" -> the ZENITH export that turns it into the page.
RENDER_CALL = {"report": "renderReport", "issue_map": "renderIssue"}


def chrome_path() -> Path | None:
    """The first Chromium this machine actually has, or None."""
    override = os.environ.get(_ENV_OVERRIDE)
    if override and Path(override).exists():
        return Path(override)
    for candidate in _CANDIDATE_PATHS:
        if Path(candidate).exists():
            return Path(candidate)
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return Path(found)
    # Playwright's own default location, whatever version it installed.
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")) if os.environ.get(
        "PLAYWRIGHT_BROWSERS_PATH") else None
    if root and root.is_dir():
        for chrome in sorted(root.glob("chromium-*/chrome-linux/chrome")):
            if chrome.exists():
                return chrome
    return None


def chromium_available() -> bool:
    return chrome_path() is not None


def render_payload_to_pdf(html_document: str, kind: str, payload: dict) -> bytes:
    """Render `payload` through the SAME code path the browser uses, and
    print it to PDF bytes. `kind` is "report" or "issue_map"."""
    if kind not in RENDER_CALL:
        raise ValueError(f"no PDF renderer for kind={kind!r}")
    if not chromium_available():
        raise RuntimeError("Chromium is not available in this environment; PDF export needs it.")

    from playwright.sync_api import sync_playwright

    render_call = RENDER_CALL[kind]
    payload_json = json.dumps(payload, default=str)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(chrome_path()), args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.set_content(html_document, wait_until="load")
            # The nav bar (Search / Issue Map / Network / Settings tabs) means
            # nothing on a static file someone is reading in an email — hide
            # it, then render straight into the same #view the live app uses,
            # through the app's own render function so a PDF can never show
            # something the live page wouldn't.
            page.evaluate(
                "(payload) => {"
                "  const nav = document.querySelector('nav.nav'); if (nav) nav.style.display = 'none';"
                "  const view = document.getElementById('view');"
                f"  window.ZENITH.{render_call}(view, payload, {{}});"
                "}",
                json.loads(payload_json),
            )
            # Let the SVG graph and any canvas finish painting before printing.
            page.wait_for_timeout(500)
            return page.pdf(
                format="A4", print_background=True,
                margin={"top": "14mm", "bottom": "14mm", "left": "10mm", "right": "10mm"},
            )
        finally:
            browser.close()

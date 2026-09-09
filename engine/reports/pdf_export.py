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
    print it to PDF bytes. `kind` is "report" or "issue_map".

    Driven through Chromium's own `--headless --print-to-pdf` rather than
    Playwright. Playwright is not in the deployed requirements and pulls its
    own browser download on install; the `chromium` system package is one apt
    line and about a tenth the size, which is what makes shipping this to a
    2GB instance reasonable at all.

    The page renders ITSELF: the payload is embedded in the document and the
    app's own `window.ZENITH.*` function is called on load, so the PDF cannot
    show anything the live page would not.
    """
    if kind not in RENDER_CALL:
        raise ValueError(f"no PDF renderer for kind={kind!r}")
    chrome = chrome_path()
    if chrome is None:
        raise RuntimeError("Chromium is not available in this environment; PDF export needs it.")

    import subprocess
    import tempfile

    render_call = RENDER_CALL[kind]
    # Embedded as JSON inside a <script type="application/json">, so no amount
    # of quoting in the payload can break out into executable code.
    payload_json = json.dumps(payload, default=str).replace("</", "<\\/")
    boot = (
        '<script type="application/json" id="pdf-payload">' + payload_json + "</script>"
        "<script>window.addEventListener('load', function(){"
        "  var nav = document.querySelector('nav.nav'); if (nav) nav.style.display='none';"
        "  var data = JSON.parse(document.getElementById('pdf-payload').textContent);"
        f"  window.ZENITH.{render_call}(document.getElementById('view'), data, {{}});"
        "  document.documentElement.setAttribute('data-pdf-ready','1');"
        "});</script>"
    )
    # Splice before the LAST </body>, not the first.
    #
    # `.replace("</body>", ..., 1)` hits the first occurrence, and the first
    # one in this document is not the real closing tag — it is inside a
    # JavaScript string, in the client-side "download as HTML" helper that
    # builds a document by concatenation:
    #
    #     +view.innerHTML+'</main></div></body></html>';
    #
    # The boot block therefore landed inside a string literal, mid-script, and
    # its own </script> closed the page's script tag early. Every remaining
    # line of JavaScript then rendered as visible body text: an eighteen-page
    # PDF of source code where the report should have been.
    #
    # rfind targets the document's actual closing tag, which is by definition
    # the last one.
    closing = html_document.rfind("</body>")
    document = (html_document[:closing] + boot + html_document[closing:]
                if closing != -1 else html_document + boot)

    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / "report.html"
        target = Path(work) / "report.pdf"
        source.write_text(document, encoding="utf-8")
        result = subprocess.run(
            [str(chrome), "--headless=new", "--disable-gpu", "--no-sandbox",
             "--disable-dev-shm-usage", "--no-first-run", "--hide-scrollbars",
             # Lets the render function and any SVG painting finish before the
             # print is taken, without a fixed sleep that is either too short
             # on a big report or wasted on a small one.
             "--virtual-time-budget=8000",
             "--run-all-compositor-stages-before-draw",
             f"--print-to-pdf={target}", "--no-pdf-header-footer",
             source.as_uri()],
            capture_output=True, timeout=120,
        )
        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError(
                "Chromium produced no PDF "
                f"(exit {result.returncode}): {result.stderr.decode('utf-8', 'replace')[:400]}")
        return target.read_bytes()

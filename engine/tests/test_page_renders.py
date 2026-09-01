"""Load the real page in a real browser and render a real payload through it.

Every frontend test until now was a string match against the source. That
cannot see a runtime error, and a runtime error in this single-file app is not
a degraded section — it is a blank page. Two defects found the first time this
ran:

  - `localStorage.getItem` at module top level, unguarded. Reading storage
    THROWS (not returns null) in a sandboxed iframe, in private browsing with
    site data blocked, and under some corporate policies. The whole script died
    before a single element was drawn.

  - `cov.mentions_analyzed || s.totalAnalyzed`: a digest that read ZERO fell
    through to the sentiment count, so the card printed "74 · 5 of 5 passes
    failed" — contradicting itself on one line.

The payload fixture is a run where the model died: it exercises every warning
path at once, which is exactly the state the page most needs to render.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
FIXTURE = Path(__file__).parent / "fixtures" / "broken_run_report.json"

pytestmark = pytest.mark.skipif(not CHROME.exists(),
                                reason="Chromium not available in this environment")


def _server(html: str, payload: dict):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj):
            self._send(json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            if self.path.startswith("/api/report/"):
                self._json({"ok": True, "status": "done", "report": payload})
            elif self.path.startswith("/api/latest"):
                self._json({"ok": False})
            elif self.path.startswith("/api/progress"):
                self._json({"ok": True, "status": "running", "stage": "Analysing"})
            elif self.path.startswith("/api/") or self.path.startswith("/health"):
                self._json({"ok": True})
            else:
                self._send(html.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if self.path.rstrip("/") == "/api/report":
                self._json({"ok": True, "job_id": "job1"})
            else:
                self.do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module")
def rendered():
    from engine.api_server import render_frontend_document

    payload = json.loads(FIXTURE.read_text())
    server = _server(render_frontend_document(), payload)
    port = server.server_address[1]
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            page.fill("#q", "Edwin Sifuna")
            page.click("#grun")
            page.wait_for_timeout(5000)
            text = page.evaluate("() => document.body.innerText")
            cards = page.evaluate("() => document.querySelectorAll('.card').length")
            # Leave the tab and come back. `go()` used to do v.innerHTML='' on
            # every switch, which destroyed the view a run was still writing
            # into — the run continued, its output went nowhere, and returning
            # showed a blank form as though it had stopped.
            page.click("button:has-text('Issue Map')")
            page.wait_for_timeout(500)
            issue_tab = page.evaluate("() => document.body.innerText")
            page.click("button:has-text('Search')")
            page.wait_for_timeout(500)
            after_return = page.evaluate("() => document.body.innerText")
            lookback = page.evaluate("() => !!document.querySelector('#qd')")
            browser.close()
    finally:
        server.shutdown()
    return {"errors": errors, "text": text, "cards": cards,
            "issue_tab": issue_tab, "after_return": after_return,
            "lookback": lookback}


# --- the page must run at all ------------------------------------------------

def test_the_page_raises_no_runtime_errors(rendered):
    assert rendered["errors"] == [], f"the page threw: {rendered['errors']}"


def test_the_report_actually_draws(rendered):
    assert rendered["cards"] > 5, "the page loaded but drew almost nothing"


# --- a broken run must announce itself, loudly and first --------------------

def test_a_broken_run_says_it_is_not_an_analysis(rendered):
    assert "This is not an analysis" in rendered["text"]
    assert "45 of 75" in rendered["text"]


def test_failed_sections_are_named_with_their_error(rendered):
    assert "Some sections could not be produced" in rendered["text"]
    assert "public_voice" in rendered["text"]
    assert "HTTP 429" in rendered["text"]


def test_the_test_grade_warning_still_appears(rendered):
    assert "Test-grade run" in rendered["text"]


# --- the numbers on the page must not contradict each other -----------------

def test_a_failed_digest_reports_zero_read_not_the_sentiment_count(rendered):
    """"Read by the analyst: 74" printed beside "5 of 5 passes failed" — the
    `||` fallback substituted a different number for a real zero."""
    text = rendered["text"]
    index = text.find("READ BY THE ANALYST")
    assert index >= 0
    block = text[index:index + 80]
    assert "5 of 5 passes failed" in block
    assert "\n0\n" in block, f"expected a zero, got: {block!r}"


def test_narratives_render_with_a_real_label_and_a_way_in(rendered):
    assert "Kitale mega rally" in rendered["text"]
    assert "mention behind this" in rendered["text"], "no way to open the narrative"
    assert "narrative-" not in rendered["text"]


def test_source_failures_reach_the_page(rendered):
    assert "Some sources did not deliver" in rendered["text"]


# --- a run must survive the reader looking at something else ----------------

def test_the_report_is_still_there_after_visiting_another_tab(rendered):
    """Switching tabs destroyed the view a run was writing into. The run kept
    going; its output had nowhere to land, and coming back showed an empty
    form as though it had stopped."""
    assert "Kitale mega rally" in rendered["after_return"], (
        "the report was destroyed by a tab switch")


def test_the_other_tab_still_renders_its_own_content(rendered):
    assert "Map a principal" in rendered["issue_tab"], "the issue map tab did not draw"


def test_switching_tabs_raises_no_errors(rendered):
    assert rendered["errors"] == []


# --- the look-back is on the form -------------------------------------------

def test_the_search_form_offers_a_look_back(rendered):
    """The window was hard-coded to 210 days, so every report was dominated by
    material half a year old."""
    assert rendered["lookback"], "no look-back selector on the search form"

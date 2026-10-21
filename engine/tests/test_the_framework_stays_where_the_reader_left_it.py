"""The Sentiment Framework section keeps disappearing at the end of the run.
It looks like it runs on a loop at times.

An investor, about a live report. The framework was there the whole time —
the page kept walking away from it. Every section that landed redrew the
report from scratch, and the redraw started at the Overview tab with every
section folded: a reader on the framework was thrown out of it each time a
section arrived, and once more at the end, when the final render also
dropped the scroll position the partial ones had held. Read from the
outside, that is a section that appears, vanishes and comes back — a loop.

And before the framework existed its tab did not either, so the tab bar
itself changed shape mid-run.

These drive the real page in a real browser, because every part of this is
behaviour — a tab, a fold, a scroll offset — and none of it is visible to a
string match against the source.
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


def _server(html: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body, ctype = html.encode(), "text/html; charset=utf-8"
            if self.path.startswith("/api/") or self.path.startswith("/health"):
                body, ctype = b'{"ok": false}', "application/json"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


#: One run as the reader lives it: the framework not built yet, the reader
#: choosing its tab, the framework landing, the reader opening a section and
#: scrolling into it, and the run finishing.
A_RUN = """(full) => {
  const Z = window.ZENITH, seen = {};
  const out = document.createElement('div');
  document.body.appendChild(out);
  const tab = label => [...out.querySelectorAll('button')]
      .find(b => b.textContent.includes(label));
  const fw = () => out.querySelector('[data-mode="sentiment"]');

  const early = Object.assign({}, full);
  delete early.sentiment_framework;
  Z.renderReport(out, early, {live: true, partial: true});
  seen.tabBeforeItExists = !!tab('Sentiment Framework');
  seen.placeholder = (fw() && fw().innerText) || '';

  tab('Sentiment Framework').click();
  Z.renderReport(out, full, {live: true, partial: true});
  seen.modeWhenItLands = out.dataset.mode;
  seen.visibleWhenItLands = !fw().classList.contains('mode-off');

  const folded = [...fw().querySelectorAll('.sect:not([open])')][0];
  folded.querySelector('.sect-head').click();
  seen.openedTitle = folded.dataset.title;
  document.body.style.minHeight = '6000px';
  window.scrollTo(0, 900);
  seen.yBefore = window.scrollY;

  Z.renderReport(out, full, {ok: true, live: true});
  seen.modeAtTheEnd = out.dataset.mode;
  seen.visibleAtTheEnd = !fw().classList.contains('mode-off');
  seen.stillOpen = [...fw().querySelectorAll('.sect[open]')]
      .some(s => s.dataset.title === seen.openedTitle);
  seen.yAfter = window.scrollY;

  Z.renderReport(out, Object.assign({}, full, {name: 'Another Subject'}), {ok: true, live: true});
  seen.modeForANewSubject = out.dataset.mode;
  return seen;
}"""


@pytest.fixture(scope="module")
def run():
    from engine.api_server import render_frontend_document

    payload = json.loads(FIXTURE.read_text())
    assert payload.get("sentiment_framework"), "the fixture must carry a framework"
    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderReport")
            seen = page.evaluate(A_RUN, payload)
            browser.close()
    finally:
        server.shutdown()
    return seen, errors


def test_the_framework_has_a_tab_before_it_exists(run):
    seen, errors = run
    assert not errors, errors
    assert seen["tabBeforeItExists"], "the tab bar must not change shape mid-run"
    assert "Building the Sentiment Framework" in seen["placeholder"]


def test_a_reader_on_the_framework_stays_on_it_as_sections_land(run):
    seen, _ = run
    assert seen["modeWhenItLands"] == "sentiment"
    assert seen["visibleWhenItLands"]


def test_the_end_of_the_run_does_not_throw_the_reader_out(run):
    seen, _ = run
    assert seen["modeAtTheEnd"] == "sentiment", "the final render reset the tab"
    assert seen["visibleAtTheEnd"]
    assert seen["stillOpen"], f"{seen['openedTitle']!r} was folded by the final render"
    assert abs(seen["yAfter"] - seen["yBefore"]) <= 2, (
        f"scrolled from {seen['yBefore']} to {seen['yAfter']}")


def test_a_new_subject_starts_at_the_top(run):
    seen, _ = run
    assert seen["modeForANewSubject"] == "overview"


#: The Issue Map redraws as each analyst lands, and its framework arrives
#: late. A reader who has not picked a tab is moved to the framework when it
#: lands — it is the deliverable — and one who has picked a tab is left on it.
AN_ISSUE_MAP_RUN = """(m) => {
  const Z = window.ZENITH, seen = {};
  const tab = (out, label) => [...out.querySelectorAll('button')]
      .find(b => b.textContent.includes(label));

  const idle = document.createElement('div'); document.body.appendChild(idle);
  Z.renderIssue(idle, Object.assign({}, m, {issue_framework: null}));
  seen.idleBefore = idle.dataset.mode;
  Z.renderIssue(idle, Object.assign({}, m, {issue_framework: {}}));
  seen.idleAfter = idle.dataset.mode;

  const reader = document.createElement('div'); document.body.appendChild(reader);
  Z.renderIssue(reader, Object.assign({}, m, {issue_framework: {}}));
  tab(reader, 'Intersection detail').click();
  Z.renderIssue(reader, Object.assign({}, m, {issue_framework: {}}));
  seen.chosenAfter = reader.dataset.mode;
  return seen;
}"""


def test_the_issue_map_moves_an_idle_reader_and_keeps_a_choice():
    from engine.api_server import render_frontend_document
    from engine.tests.test_issue_map_page import _map

    issue_map = json.loads(json.dumps(_map(), default=str))
    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderIssue")
            seen = page.evaluate(AN_ISSUE_MAP_RUN, issue_map)
            browser.close()
    finally:
        server.shutdown()
    assert not errors, errors
    assert seen["idleBefore"] == "detail"
    assert seen["idleAfter"] == "framework", "the deliverable landed and the reader was not shown it"
    assert seen["chosenAfter"] == "detail", "a redraw overrode the reader's choice"

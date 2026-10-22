"""Subtitles should be on the left side.

The client, about the Weekly Dashboard and the Sentiment Framework. Every
section header spreads its parts with `justify-content: space-between`; a
section with a number badge has three parts — badge, title, Show/Hide — so
its title and subtitle sat in the middle of the bar, while every
un-numbered section's sat on the left. Two layouts for one kind of heading,
and the centred one read as a different kind of thing.

Measured in a real browser, because alignment is geometry and no string
match against the source can see it.
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


#: For every visible section in each of the report's three views: where its
#: title and subtitle start, against where the header (or its number badge)
#: ends on the left.
MEASURE = """(full) => {
  const out = document.createElement('div');
  document.querySelector('#zenith').appendChild(out);
  window.ZENITH.renderReport(out, full, {ok: true, live: true});
  const rows = [];
  for (const label of ['Overview', 'Weekly Dashboard', 'Sentiment Framework']) {
    const tab = [...out.querySelectorAll('button')].find(b => b.textContent.includes(label));
    if (!tab) continue;
    tab.click();
    out.querySelectorAll('.sect').forEach(s => {
      if (!s.offsetParent) return;
      const head = s.querySelector('.sect-head').getBoundingClientRect();
      const badge = s.querySelector('.sect-no');
      const start = badge ? badge.getBoundingClientRect().right : head.left;
      const title = s.querySelector('.sect-head .lbl').getBoundingClientRect();
      const sub = s.querySelector('.sect-sub');
      rows.push({view: label, section: s.dataset.title, numbered: !!badge,
                 title: Math.round(title.left - start),
                 sub: sub ? Math.round(sub.getBoundingClientRect().left - start) : null});
    });
  }
  return rows;
}"""


@pytest.fixture(scope="module")
def headers():
    from engine.api_server import render_frontend_document

    payload = json.loads(FIXTURE.read_text())
    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderReport")
            rows = page.evaluate(MEASURE, payload)
            browser.close()
    finally:
        server.shutdown()
    assert not errors, errors
    return rows


def test_numbered_sections_were_measured(headers):
    assert any(r["numbered"] for r in headers), "no numbered section rendered"
    assert any(not r["numbered"] for r in headers), "no plain section rendered"


def test_every_title_and_subtitle_starts_on_the_left(headers):
    # Within a gap's width of where the header's own content starts — the
    # badge's right edge, or the header's padding. Centred, on a 1280px page,
    # they started hundreds of pixels in.
    off = [r for r in headers
           if r["title"] > 40 or (r["sub"] is not None and r["sub"] > 40)]
    assert not off, f"titles or subtitles away from the left edge: {off}"


def test_a_subtitle_lines_up_under_its_title(headers):
    misaligned = [r for r in headers if r["sub"] is not None and abs(r["sub"] - r["title"]) > 2]
    assert not misaligned, misaligned

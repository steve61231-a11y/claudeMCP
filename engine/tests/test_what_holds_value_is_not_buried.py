"""Two notes from the client, about things that hold value and could not be seen.

"There's a drop down that has two things — something about the drivers,
and another section there. Separate the two into their own two different
sections, because each one of them holds a lot of value." Share of Voice
was a dropdown folded inside Drivers, and folded it was the half of the
answer nobody opened.

"Some things are not bold enough … even if they drop down, they blend into
the sentences and you can't see them." The dropdowns inside a section were
a grey caption with a ten-pixel triangle. Evidence, sources and the rest of
the brief sat behind controls a reader read past.

Both are about what a reader can SEE, so both are measured in a browser.
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


READ = """(full) => {
  const out = document.createElement('div');
  document.querySelector('#zenith').appendChild(out);
  window.ZENITH.renderReport(out, full, {ok: true, live: true});
  [...out.querySelectorAll('button')].find(b => b.textContent.includes('Weekly Dashboard')).click();
  const weekly = out.querySelector('[data-mode="weekly"]');
  const sections = [...weekly.querySelectorAll(':scope > .sect')].map(s => s.dataset.title);
  const drivers = [...weekly.querySelectorAll(':scope > .sect')].find(s => s.dataset.title === 'Drivers');
  weekly.querySelectorAll('.sect').forEach(s => s.setAttribute('open', ''));
  // Every view's dropdowns, shown or not: computed style does not depend
  // on the view being the one on screen.
  const folds = [...out.querySelectorAll('summary.fold-summary')];
  return {
    sections,
    sovInsideDrivers: drivers ? drivers.innerText.includes('Share of voice') : null,
    folds: folds.map(f => {
      const cs = getComputedStyle(f), after = getComputedStyle(f, '::after');
      return {text: f.textContent.trim().slice(0, 40), border: parseFloat(cs.borderTopWidth),
              weight: parseInt(cs.fontWeight, 10), pill: after.content};
    }),
  };
}"""


@pytest.fixture(scope="module")
def page_state():
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
            seen = page.evaluate(READ, payload)
            browser.close()
    finally:
        server.shutdown()
    assert not errors, errors
    return seen


def test_share_of_voice_is_its_own_section_after_drivers(page_state):
    sections = page_state["sections"]
    assert "Share of Voice" in sections, sections
    assert sections.index("Share of Voice") == sections.index("Drivers") + 1
    assert page_state["sovInsideDrivers"] is False


def test_every_dropdown_looks_like_a_control(page_state):
    folds = page_state["folds"]
    assert folds, "no dropdown rendered — the check would pass on nothing"
    faint = [f for f in folds if f["border"] < 1 or f["weight"] < 600]
    assert not faint, f"dropdowns that blend into the text: {faint}"
    unlabelled = [f for f in folds if "Show" not in f["pill"] and "Hide" not in f["pill"]]
    assert not unlabelled, f"dropdowns that do not say what clicking does: {unlabelled}"

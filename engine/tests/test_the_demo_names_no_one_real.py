"""The sample report may not say anything about anyone real.

The page shows a sample report when the engine cannot be reached. It was
about William Ruto: invented findings ("~70% of negative reach traces to
under a dozen accounts"), invented quotes attributed to Citizen TV and the
Daily Nation, and links to made-up articles on their real domains — shown
to whoever was looking when the engine was briefly unreachable, a client
searching someone else among them. The founder, 22 October 2026: "We don't
want fake data. If we say that this was said … we want it to be legit."

The sample is kept, because it shows a buyer what a report contains, and
moved to a fictional governor of a fictional county. Pinned as rules rather
than a list of names: every link is on a domain reserved for examples
(RFC 2606), the subject says it is fictional, and no outlet the engine
itself knows to be real appears in it — the same outlet lists
`reports/outlets.py` classifies the live corpus with.
"""

import json
import re
import shutil
import subprocess
from urllib.parse import urlsplit

import pytest

from engine.api_server import render_frontend_document
from engine.reports import outlets

PAGE = render_frontend_document()


def _demo() -> dict:
    if not shutil.which("node"):
        pytest.skip("node not installed")
    start = PAGE.index("const DEMO={")
    end = PAGE.index("\n  };", start) + len("\n  }")
    script = ("const DEMO=" + PAGE[start + len("const DEMO="): end]
              + ";process.stdout.write(JSON.stringify(DEMO));")
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout)


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def test_every_link_in_the_sample_goes_nowhere_real():
    links = {u for s in _strings(_demo()) for u in re.findall(r"https?://[^\s'\"]+", s)}
    assert links, "the sample has no links to check — the test is looking in the wrong place"
    real = sorted(u for u in links if not (urlsplit(u).hostname or "").endswith(".example"))
    assert not real, f"the sample links to real sites: {real}"


def test_the_sample_subject_says_it_is_fictional():
    demo = _demo()
    assert "fictional" in demo["report"]["name"].lower()
    assert "fictional" in demo["issue"]["principal"].lower()


def test_no_outlet_the_engine_knows_to_be_real_appears_in_the_sample():
    text = "\n".join(_strings(_demo()))
    lowered = text.lower()
    domains = set(outlets.KENYAN_DOMAINS) | set(outlets.DOMAIN_NAMES)
    found = {d for d in domains if d in lowered}
    names = {n for n in set(outlets.DOMAIN_NAMES.values()) | set(outlets.CANONICAL_NAMES.values())
             if re.search(r"\b" + re.escape(n) + r"\b", text)}
    known_keys = outlets.KENYAN_NAMES | outlets.INTERNATIONAL_NAMES | set(outlets.CANONICAL_NAMES)
    keyed = {s for s in _strings(_demo())
             if len(s) < 40 and re.sub(r"[^a-z0-9]", "", s.lower()) in known_keys}
    assert not (found or names or keyed), (
        f"real outlets in the sample: {sorted(found | names | keyed)}")


def test_a_search_the_engine_never_received_says_so_above_the_sample():
    """The note explaining the sample was appended and then wiped: the
    report renderer clears its container first, so a search made while the
    engine was unreachable showed a full report about someone else with
    nothing but a "Cached / demo" pill to say so."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    chrome = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    if not chrome.exists():
        pytest.skip("Chromium not available in this environment")

    class Unreachable(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/api/"):
                self.send_response(503)
                self.end_headers()
                return
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_GET

    server = ThreadingHTTPServer(("127.0.0.1", 0), Unreachable)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=str(chrome))
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.fill("#q", "Someone Real")
            page.keyboard.press("Enter")
            page.wait_for_function(
                "() => document.querySelector('#view').innerText.includes('(fictional)')",
                timeout=15000)
            seen = page.evaluate("""() => {
              const text = document.querySelector('#view').innerText;
              return {note: text.indexOf('could not be reached'),
                      sample: text.indexOf('(fictional)')};
            }""")
            browser.close()
    finally:
        server.shutdown()
    assert seen["note"] >= 0, "the sample was shown with no word that nothing was searched"
    assert seen["note"] < seen["sample"], "the note sits below the sample it explains"

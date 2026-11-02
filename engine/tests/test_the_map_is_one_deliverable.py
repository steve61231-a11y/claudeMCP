"""The Issue Map is one deliverable, and its PDF is the browser's own print.

Phase 5 of docs/ISSUE_MAP_FOUNDATION.md. The map had two tabs — "Issue
Framework" and "Intersection detail" — and the page kept only the open one
in the DOM, so a printed map was whichever tab happened to be showing: the
framework without the analysis it rests on, or the analysis without the
framework the client asked for. Its "Download PDF" asked the server to run a
headless Chromium, which a Railway instance does not carry; the button fell
back to an HTML file.

Now the framework leads and the intersection detail sits beneath it as
"The working" — folded on screen, open on paper, on its own pages — and the
framework's graph filters the working's sections, so the page carries one
graph, not two. "Save as PDF" prints the page the reader is looking at.
"""

from __future__ import annotations

import pytest

from engine.tests.test_the_framework_stays_where_the_reader_left_it import CHROME, _server

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

pytestmark = pytest.mark.skipif(not CHROME.exists(), reason="Chromium not available in this environment")

RENDER = """(m) => {
  const Z = window.ZENITH;
  const out = document.createElement('div'); out.id = 'map';
  document.querySelector('#zenith').appendChild(out);
  let printed = 0; window.print = () => { printed += 1; };
  Z.renderIssue(out, m);
  const fold = out.querySelector('details.the-working');
  const buttons = [...out.querySelectorAll('button')].map(b => b.textContent.trim());
  out.querySelector('#dlIssuePdf').click();
  return {
    hasFramework: !!m.issue_framework,
    tabs: buttons.filter(t => /Intersection detail|Issue Framework/.test(t)),
    fold: !!fold, foldOpen: fold && fold.open,
    frameworkFirst: fold ? !!(out.querySelector('.grid').firstElementChild.compareDocumentPosition(fold)
                              & Node.DOCUMENT_POSITION_FOLLOWING) : false,
    graphs: out.querySelectorAll('svg .gnode').length ? [...out.querySelectorAll('svg')]
              .filter(s => s.querySelector('.gnode')).length : 0,
    workingHasTheAnalysis: fold ? /Verdict|Involvement|Key actors|Sub-issues/.test(fold.textContent) : false,
    printed,
    message: out.querySelector('#dlIssueMsg').textContent,
  };
}"""


@pytest.fixture(scope="module")
def seen():
    import json

    from engine.api_server import render_frontend_document
    from engine.reports import issue_framework
    from engine.tests.test_issue_map_page import _map

    m = _map()
    actors = [{"name": "National Treasury", "entity_type": "institution", "position": "for"},
              {"name": "Okiya Omtatah", "entity_type": "person", "position": "against"}]
    m["issue_framework"] = issue_framework.build(m["issue"], m["principal"], m, actors, [], [])
    m = json.loads(json.dumps(m, default=str))
    server = _server(render_frontend_document())
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME), args=["--no-sandbox"])
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/", wait_until="load")
            page.wait_for_function("() => window.ZENITH && window.ZENITH.renderIssue")
            got = page.evaluate(RENDER, m)
            screen_text = page.inner_text("#map")
            page.emulate_media(media="print")
            print_text = page.inner_text("#map")
            browser.close()
    finally:
        server.shutdown()
    return got, errors, screen_text, print_text


def test_the_framework_and_its_working_are_one_page(seen):
    got, errors, _, _ = seen
    assert not errors, errors
    assert got["hasFramework"], "the map must carry a framework for this test to mean anything"
    assert not got["tabs"], f"the map still switches between tabs: {got['tabs']}"
    assert got["fold"] and got["frameworkFirst"], "the working must sit beneath the framework"
    assert got["workingHasTheAnalysis"], "the intersection detail did not reach the working"


def test_the_working_is_folded_on_screen_and_open_on_paper(seen):
    got, _, screen_text, print_text = seen
    assert not got["foldOpen"]
    assert "Key actors" not in screen_text or "Key actors" in print_text
    assert len(print_text) > len(screen_text), "the printed map is no longer than the folded screen"
    assert "Open the working" not in print_text, "a screen control printed with nothing to open"


def test_the_page_has_one_graph(seen):
    got, _, _, _ = seen
    assert got["graphs"] <= 1, f"{got['graphs']} graphs drawn; the working filters on the framework's"


def test_save_as_pdf_is_the_browsers_print(seen):
    got, _, _, _ = seen
    assert got["printed"] == 1
    assert "Save as PDF" in got["message"]

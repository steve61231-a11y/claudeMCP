"""Drive Issue Mapping in a real browser and check the map is a query
interface, not a picture.

Three defects this catches, all of which shipped looking fine:

  - Switching tabs stacked the views on top of each other, because
    `#zenith .grid{display:grid}` outranks the browser's own `[hidden]` rule.
  - Selecting a node changed the graph and nothing else, so the graph and the
    sections below it could disagree about what was related to what.
  - `GRAPH_SELECTION.listeners` was a module-global array that every render
    pushed onto. The map re-renders each time another analyst lands, so by the
    end of a run the selection was firing into detached DOM.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from engine.reports import issue_graph  # noqa: E402

CHROME = Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
pytestmark = pytest.mark.skipif(not CHROME.exists(),
                                reason="Chromium not available in this environment")

ANALYSIS = {
    "key_actors": [
        {"name": "Okiya Omtatah", "entity_type": "person", "position": "against",
         "influence": 80, "relation": "Filed the petition.",
         "quotes": [{"text": "Omtatah filed the case", "url": "https://n/1"}]},
        {"name": "National Treasury", "entity_type": "organization", "position": "for",
         "influence": 70, "relation": "Defends the borrowing.",
         "quotes": [{"text": "Treasury defended", "url": "https://n/2"}]},
    ],
    "linking_narratives": [{"narrative": "Odious debt doctrine", "strength": 60,
                            "summary": "s",
                            "quotes": [{"text": "doctrine cited", "url": "https://n/3"}]}],
    "timeline": [
        {"date": "2026-06-01", "event": "Okiya Omtatah files petition at the High Court",
         "sources": 3, "quotes": [{"text": "filed", "url": "https://n/4"}]},
        {"date": "2026-07-02", "event": "National Treasury responds to the petition",
         "sources": 2, "quotes": []},
    ],
    "sub_issues": [
        {"sub_issue": "Whether the loan agreements can be withheld from Parliament",
         "question": "Are the agreements public documents?", "root": True,
         "actors": ["National Treasury"], "detail": "d",
         "quotes": [{"text": "withheld", "url": "https://n/6"}]},
    ],
    "involvement": "The senator is challenging the debt.",
    "verdict": "contested",
}


def _map():
    graph = issue_graph.build(
        "Okiya Omtatah", "International Monetary Fund (IMF)", ANALYSIS, None,
        corpus=[{"platform": "nation.africa", "text": "t", "source_url": "https://n/5"}])
    return {"principal": "Okiya Omtatah", "issue": "International Monetary Fund (IMF)",
            "intersection": ANALYSIS, "issue_graph": graph,
            "coverage": {"mentions_total": 40, "mentions_analyzed": 40},
            "evidence_sample": [{"platform": "nation.africa", "text": "An article",
                                 "url": "https://n/9"}],
            "research_plan": [{"dimension": "conflict", "why": "who is on the other side",
                               "queries": ['"Omtatah" "IMF" criticism']}],
            "thin": False, "issue_framework": None}


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

        def do_GET(self):
            if self.path.startswith(("/api/report/", "/api/issue-map/")):
                self._send(json.dumps({"ok": True, "status": "done",
                                       "issue_map": payload}).encode(), "application/json")
            elif self.path.startswith(("/api/", "/health")):
                self._send(json.dumps({"ok": False}).encode(), "application/json")
            else:
                self._send(html.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if "/api/issue-map" in self.path:
                self._send(json.dumps({"ok": True, "job_id": "j1"}).encode(),
                           "application/json")
            else:
                self.do_GET()

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


SECTIONS = """() => {
  const t = document.body.innerText;
  const grab = h => { const m = t.match(new RegExp(h + '\\n([^\\n]*)', 'i')); return m ? m[1] : ''; };
  return {timeline: grab('timeline'), actors: grab('key actors'),
          narratives: grab('linking narratives'), evidence: grab('evidence sample'),
          subIssues: grab('sub-issues')};
}"""


@pytest.fixture(scope="module")
def mapped():
    from engine.api_server import render_frontend_document
    srv = _server(render_frontend_document(), _map())
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=str(CHROME),
                                         args=["--no-sandbox"])
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
            page.click("button:has-text('Issue Map')")
            page.wait_for_timeout(300)
            visible = page.evaluate(
                "() => Array.from(document.querySelectorAll('#view > *'))"
                ".filter(e => !e.hidden).length")
            page.fill("#pr", "Okiya Omtatah")
            page.fill("#is", "International Monetary Fund (IMF)")
            page.click("#mrun")
            page.wait_for_selector(".gnode", timeout=15000)
            yield page, errors, visible, browser
            browser.close()
    finally:
        srv.shutdown()


def test_switching_to_issue_map_replaces_the_workspace(mapped):
    _page, _errors, visible, _b = mapped
    assert visible == 1, "views stacked instead of replacing each other"


def test_the_page_runs_without_throwing(mapped):
    _page, errors, _v, _b = mapped
    assert errors == []


def test_the_graph_draws_every_node(mapped):
    page, _e, _v, _b = mapped
    assert page.locator(".gnode").count() == len(_map()["issue_graph"]["nodes"])


def test_the_research_plan_is_shown(mapped):
    page, _e, _v, _b = mapped
    assert "how this was researched" in page.inner_text("body").lower()


def test_the_issue_is_broken_into_sub_issues(mapped):
    body = mapped[0].inner_text("body").lower()
    assert "sub-issues" in body
    assert "withheld from parliament" in body
    assert "what is contested" in body


def test_the_answer_comes_before_the_diagram(mapped):
    """The graph is a navigation aid over the findings, not the findings.
    Placed first it was the first thing on the page: circles and dotted lines
    where the verdict should be."""
    page, _e, _v, _b = mapped
    body = page.inner_text("body").lower()
    assert body.index("verdict") < body.index("the map"), \
        "the diagram is above the analysis"


def test_the_line_styles_are_explained_on_the_page(mapped):
    body = mapped[0].inner_text("body").lower()
    assert "solid line" in body and "dotted line" in body


def test_selecting_a_node_synchronises_every_section(mapped):
    page, _e, _v, _b = mapped
    before = page.evaluate(SECTIONS)
    labels = page.evaluate(
        "() => Array.from(document.querySelectorAll('.gnode')).map(n => n.textContent)")
    idx = next(i for i, t in enumerate(labels) if "Treasury" in t)
    nodes = page.query_selector_all(".gnode")
    nodes[idx].click()
    page.wait_for_timeout(250)

    selected = page.evaluate(SECTIONS)
    body = page.inner_text("body").lower()
    assert "connected to" in body, "no detail panel for the selected node"
    assert "national treasury" in selected["timeline"].lower(), \
        "the timeline did not follow the selection"
    assert "1 of 1" in selected["subIssues"], "the sub-issues did not follow the selection"
    assert selected != before, "selecting a node changed nothing below the graph"

    # Clicking it again clears the selection and every section comes back.
    page.query_selector_all(".gnode")[idx].click()
    page.wait_for_timeout(250)
    assert page.evaluate(SECTIONS) == before


def test_a_graph_too_thin_to_read_is_not_drawn(mapped):
    """Four dots and two lines tell a reader nothing the actor list did not
    already say. Drawing it anyway is how a thin result comes to look like an
    analytical one."""
    import threading

    from engine.api_server import render_frontend_document

    browser = mapped[3]

    thin = _map()
    thin["issue_graph"] = {
        "nodes": [{"id": "a", "type": "issue", "label": "IMF", "color": "#fff", "weight": 1,
                   "evidence": []}],
        "edges": [], "legend": [{"type": "issue", "color": "#fff", "count": 1}],
        "stats": {"nodes": 1, "edges": 0, "isolated": 1},
    }
    srv = _server(render_frontend_document(), thin)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/", wait_until="load")
        page.click("button:has-text('Issue Map')")
        page.fill("#pr", "Okiya Omtatah")
        page.fill("#is", "International Monetary Fund (IMF)")
        page.click("#mrun")
        page.wait_for_selector("text=Intersection detail", timeout=15000)
        page.wait_for_timeout(500)
        body = page.inner_text("body").lower()
        assert "too little connected material" in body
        assert page.locator(".gnode").count() == 0
        assert "verdict" in body   # the findings are still all there
        page.close()
    finally:
        srv.shutdown()

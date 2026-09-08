"""Send the report to someone outside the app — a file, not a link.

The client wants to hand a finished report to an outside analyst without
giving them a URL into a live, API-key-gated system. These endpoints render
the exact stored payload through the app's own rendering code
(window.ZENITH.renderReport / renderIssue) and return it as a downloadable
PDF — so a PDF can never show something the live page wouldn't.
"""

import pytest
from fastapi.testclient import TestClient

from engine.reports import pdf_export

pytestmark = pytest.mark.skipif(not pdf_export.chromium_available(),
                                reason="Chromium not available in this environment")


@pytest.fixture()
def client(monkeypatch):
    from engine import api_server

    monkeypatch.setattr(api_server, "_require_api_key", lambda x: None)
    return TestClient(api_server.app)


def _seed_progress(monkeypatch, kind: str, subject_key: str, payload: dict):
    from engine import api_server

    stored = {kind: {"payload": payload}}
    monkeypatch.setattr(
        api_server, "_read_progress",
        lambda key, k: stored.get(k) if key == subject_key else None)


def test_downloading_a_report_returns_a_real_pdf(client, monkeypatch):
    from engine import api_server

    payload = {"name": "Test Subject", "window": "2026-06-01 – 2026-06-22",
              "sentiment": {"positive": 40, "neutral": 35, "negative": 25},
              "volume": {"total": 10, "platforms": 2}, "narratives": [],
              "influence": [], "coverage": {}, "grade": {"production": False}}
    _seed_progress(monkeypatch, "report", api_server._subject_key("Test Subject"), payload)

    res = client.get("/api/report/download?name=Test%20Subject")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content[:5] == b"%PDF-"
    assert "attachment" in res.headers["content-disposition"]
    assert "test-subject" in res.headers["content-disposition"]


def test_downloading_an_issue_map_returns_a_real_pdf(client, monkeypatch):
    from engine import api_server

    payload = {"principal": "Okiya Omtatah", "issue": "IMF",
              "intersection": {"verdict": "Contested.", "key_actors": [],
                               "linking_narratives": [], "timeline": [], "sub_issues": []},
              "issue_graph": {"nodes": [], "edges": [], "legend": [],
                              "stats": {"nodes": 0, "edges": 0}},
              "coverage": {}, "evidence_sample": [], "thin": False, "issue_framework": None}
    key = api_server._subject_key("Okiya Omtatah", "IMF")
    _seed_progress(monkeypatch, "issue_map", key, payload)

    res = client.get("/api/issue-map/download?principal=Okiya%20Omtatah&issue=IMF")
    assert res.status_code == 200
    assert res.content[:5] == b"%PDF-"
    assert "okiya-omtatah-x-imf" in res.headers["content-disposition"]


def test_downloading_with_nothing_stored_is_a_clean_404(client, monkeypatch):
    from engine import api_server

    monkeypatch.setattr(api_server, "_read_progress", lambda key, kind: None)
    res = client.get("/api/report/download?name=Nobody")
    assert res.status_code == 404


def test_the_pdf_is_not_trivially_empty(client, monkeypatch):
    """A blank PDF would still pass the magic-bytes check; also require a
    minimum size, since a render that silently failed tends to produce a
    tiny near-empty document."""
    from engine import api_server

    payload = {"name": "Test Subject", "sentiment": {}, "volume": {"total": 5},
              "narratives": [], "influence": [], "coverage": {}}
    _seed_progress(monkeypatch, "report", api_server._subject_key("Test Subject"), payload)

    res = client.get("/api/report/download?name=Test%20Subject")
    assert len(res.content) > 5000


# --- the button must not need a browser on the SERVER -------------------------

def test_the_download_button_prints_from_the_readers_own_browser():
    """This shipped as a server-side render against a hard-coded path to the
    developer's own container. Render has no Chromium, so the only machine a
    user ever presses this on answered:

        503 {"detail":"PDF export is not available in this environment"}

    The reader's browser has already rendered the report, so printing it there
    needs nothing installed anywhere and cannot drift from what is on screen.
    """
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    assert "window.print()" in page, "the button no longer prints client-side"
    assert "/api/report/download" not in page, \
        "the button still depends on a server-side browser"


def test_the_printed_page_is_recoloured_for_paper():
    """The UI is dark. Printed unchanged it is an unreadable slab of ink."""
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    assert "@media print" in page
    assert "@page" in page


def test_a_citation_carries_its_address_onto_the_paper():
    """A link is worthless in a printed file unless the URL is printed too —
    and the whole point of this export is that it leaves the app."""
    from engine.api_server import render_frontend_document

    assert 'a.src-link[href^="http"]::after' in render_frontend_document()


def test_chromium_is_found_rather_than_assumed(tmp_path, monkeypatch):
    """The path was hard-coded to one container's Playwright install, so this
    reported "not available" everywhere else. It is now discovered, and an
    operator can name it outright."""
    from engine.reports import pdf_export

    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv(pdf_export._ENV_OVERRIDE, str(fake))
    assert pdf_export.chrome_path() == fake
    assert pdf_export.chromium_available()


def test_no_browser_anywhere_is_reported_honestly(monkeypatch):
    from engine.reports import pdf_export

    monkeypatch.delenv(pdf_export._ENV_OVERRIDE, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(pdf_export, "_CANDIDATE_PATHS", ())
    monkeypatch.setattr(pdf_export.shutil, "which", lambda _name: None)
    assert pdf_export.chrome_path() is None
    assert not pdf_export.chromium_available()

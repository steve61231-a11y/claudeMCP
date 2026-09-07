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

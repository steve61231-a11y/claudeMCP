from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import engine.api_server as api_server
from engine import llm
from engine.db.models import LlmUsage


def test_record_usage_increments(db_session, monkeypatch):
    # Point llm._record_usage at the test session.
    monkeypatch.setattr("engine.db.session.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
    llm._record_usage(resp)
    llm._record_usage(resp)

    row = db_session.query(LlmUsage).filter_by(day=date.today()).first()
    assert row is not None
    assert row.calls == 2
    assert row.input_tokens == 2000
    assert row.output_tokens == 400


def test_metrics_endpoint_shape(db_session, monkeypatch):
    # Serve the endpoint from the test session and skip the live balance call.
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(api_server.settings, "socialcrawl_api_key", "")  # skip balance call
    monkeypatch.setattr(api_server.settings, "pulse_api_key", "")  # no auth for this test
    api_server._metrics_cache["data"] = None

    client = TestClient(api_server.app)
    body = client.get("/api/admin/metrics").json()
    assert body["ok"]
    m = body["metrics"]
    for section in ("credits", "sources", "corpus", "pipeline", "reports", "alerts", "system"):
        assert section in m, f"missing section {section}"
    # source board covers every connector + tipline
    names = {s["name"] for s in m["sources"]}
    assert {"socialcrawl", "gdelt", "twscrape", "facebook", "tipline"}.issubset(names)
    assert m["credits"]["anthropic_today"]["usd"] >= 0
    assert m["system"]["db_ok"] is True


def test_metrics_requires_api_key(monkeypatch):
    monkeypatch.setattr(api_server.settings, "pulse_api_key", "secret")
    api_server._metrics_cache["data"] = None
    client = TestClient(api_server.app)
    assert client.get("/api/admin/metrics").status_code == 401
    # wrong key rejected; correct key would pass auth (may still 500 without DB,
    # so we only assert the auth gate here)
    assert client.get("/api/admin/metrics", headers={"X-API-Key": "wrong"}).status_code == 401

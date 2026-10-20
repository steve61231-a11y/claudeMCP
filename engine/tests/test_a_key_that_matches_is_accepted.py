"""Report call failed: HTTP 401 — "it rejected the key".

After the deploy of 0e1b4ee the operator could not generate a report at all,
twice, with the same message. Two faults made that harder to clear than it
should have been, and neither was the operator's:

  * Settings said "Connected ✓" after checking only that /api/health
    answered — which it does for anyone. A wrong key looked connected until
    the first report was refused. A green tick for a check that never ran.
  * The engine compared keys EXACTLY, while the page trims what it sends.
    A PULSE_API_KEY pasted into the host's variables with a trailing space,
    a line break or the quotes from an .env file could never be matched by
    anything the page can send — two copies of one key, reported as a
    rejected key.

And the message could not say which case it was: "this browser has no key
saved" and "the key it has is not the engine's" read identically, though the
page knows which, and the engine knows which. Neither answer may say anything
about the real key — this endpoint answers anyone.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine import api_server
from engine.config import settings

PAGE = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()


@pytest.fixture()
def client():
    return TestClient(api_server.app)


@pytest.mark.parametrize("configured", [
    "s3cret-key", "s3cret-key ", " s3cret-key\n", '"s3cret-key"', "'s3cret-key'\n",
])
def test_the_key_as_meant_is_the_key(client, monkeypatch, configured):
    monkeypatch.setattr(settings, "pulse_api_key", configured)
    resp = client.get("/api/auth-check", headers={"x-api-key": "s3cret-key"})
    assert resp.status_code == 200, f"{configured!r} refused the key it holds"


def test_a_missing_key_and_a_wrong_key_are_told_apart(client, monkeypatch):
    monkeypatch.setattr(settings, "pulse_api_key", "s3cret-key")
    missing = client.get("/api/auth-check")
    wrong = client.get("/api/auth-check", headers={"x-api-key": "not-it"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["detail"] == api_server.KEY_MISSING
    assert wrong.json()["detail"] == api_server.KEY_MISMATCH


def test_the_refusal_says_nothing_about_the_real_key(client, monkeypatch):
    monkeypatch.setattr(settings, "pulse_api_key", "s3cret-key")
    body = client.get("/api/auth-check", headers={"x-api-key": "s3cret"}).text
    assert "s3cret-key" not in body and "key" in body
    assert "-key" not in body.replace("x-api-key", "").replace("API key", "")


def test_an_engine_with_no_key_is_open(client, monkeypatch):
    monkeypatch.setattr(settings, "pulse_api_key", "")
    resp = client.get("/api/auth-check")
    assert resp.status_code == 200 and resp.json()["key_required"] is False


def test_the_report_endpoints_use_the_same_check(client, monkeypatch):
    monkeypatch.setattr(settings, "pulse_api_key", "s3cret-key ")
    resp = client.get("/api/report/does-not-exist", headers={"x-api-key": "s3cret-key"})
    assert resp.status_code == 404, "the report route still compares keys exactly"


# --- the page -----------------------------------------------------------------

def test_settings_checks_the_key_not_only_the_engine():
    settings_view = PAGE[PAGE.index("function settingsView"):PAGE.index("const errCard=")]
    assert "/api/auth-check" in settings_view
    assert "key accepted" in settings_view
    assert settings_view.count("keyVerdict(") >= 3, "one of the two buttons does not check the key"
    assert "Reachable ✓" not in settings_view, "the old unchecked tick is still there"


def test_the_error_says_which_kind_of_refusal():
    cause = PAGE[PAGE.index("function httpCause"):PAGE.index("async function api(")]
    assert "this browser has no API key saved" in cause
    assert "refused the key this browser sent" in cause
    assert "staged change" in cause, "a rotated key that was never deployed is not mentioned"


def test_a_refusal_is_not_retried():
    """Three attempts four seconds apart for an answer that will not change."""
    api = PAGE[PAGE.index("async function api("):PAGE.index("async function ping()")]
    assert "[400,401,402,403,422,429].includes(" in api

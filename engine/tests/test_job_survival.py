"""A job the client is holding must not vanish underneath it.

`/api/report/{job_id}` returned 404 on a run that was going perfectly well.
Two separate causes, both of which got worse the moment runs got longer:

- the stale-job sweep evicted on age alone, so a run past the TTL was deleted
  while it was still executing,
- `_jobs` lives in this process only, and a free instance restarts, taking an
  hour of scraping and four analysts with it.
"""

import time
from datetime import datetime

from fastapi.testclient import TestClient

from engine import api_server
from engine.db.models import IntelligenceReport, Politician


def _client(monkeypatch):
    monkeypatch.setattr(api_server, "_require_api_key", lambda key: None)
    monkeypatch.setattr(api_server, "_check_rate_limit", lambda host: None)
    return TestClient(api_server.app)


# --------------------------------------------------------------------------
# Eviction
# --------------------------------------------------------------------------

def test_a_running_job_is_never_evicted_however_old():
    """Evicting one deletes the only handle its client has on work that is
    actively happening. A full-stack issue map can outlive the TTL."""
    api_server._jobs.clear()
    ancient = time.time() - (api_server._JOB_TTL_SECONDS * 5)
    api_server._jobs["still-going"] = {"status": "running", "created_at": ancient}
    try:
        api_server._evict_stale_jobs()
        assert "still-going" in api_server._jobs
    finally:
        api_server._jobs.clear()


def test_a_finished_job_past_the_ttl_is_still_evicted():
    api_server._jobs.clear()
    ancient = time.time() - (api_server._JOB_TTL_SECONDS * 5)
    api_server._jobs["done-and-old"] = {"status": "done", "ok": True, "created_at": ancient}
    api_server._jobs["done-and-recent"] = {"status": "done", "ok": True, "created_at": time.time()}
    try:
        api_server._evict_stale_jobs()
        assert "done-and-old" not in api_server._jobs
        assert "done-and-recent" in api_server._jobs
    finally:
        api_server._jobs.clear()


# --------------------------------------------------------------------------
# Surviving a restart
# --------------------------------------------------------------------------

def _use_test_session(monkeypatch, db_session):
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


def test_a_finished_issue_map_is_stored_and_recoverable(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    subject = Politician(name="Recovery Probe")
    db_session.add(subject)
    db_session.commit()

    payload = {
        "principal": "Recovery Probe", "issue": "SHA",
        "window": {"start": datetime(2026, 1, 1), "end": datetime(2026, 6, 1)},
        "intersection": {"verdict": "the map"},
    }
    api_server._store_issue_map("Recovery Probe", "SHA", payload)

    stored = db_session.query(IntelligenceReport).filter_by(politician_id=subject.id).one()
    assert stored.period == "issue:sha"
    assert api_server._lookup_issue_map("Recovery Probe", "SHA")["intersection"]["verdict"] == "the map"


def test_recovery_is_case_insensitive_on_the_issue(db_session, monkeypatch):
    """The client sends back whatever the user typed."""
    _use_test_session(monkeypatch, db_session)
    db_session.add(Politician(name="Case Probe"))
    db_session.commit()
    api_server._store_issue_map("Case Probe", "SHA", {"intersection": {}})
    assert api_server._lookup_issue_map("Case Probe", "sha") is not None


def test_nothing_stored_recovers_nothing(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    db_session.add(Politician(name="Empty Probe"))
    db_session.commit()
    assert api_server._lookup_issue_map("Empty Probe", "SHA") is None
    assert api_server._lookup_issue_map("Never Heard Of Them", "SHA") is None


def test_the_recovery_endpoint_answers_and_says_it_recovered(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    db_session.add(Politician(name="Endpoint Probe"))
    db_session.commit()
    api_server._store_issue_map("Endpoint Probe", "SHA", {"intersection": {"verdict": "v"}})

    client = _client(monkeypatch)
    resp = client.get("/api/issue-map/latest", params={"principal": "Endpoint Probe", "issue": "SHA"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["recovered"] is True
    assert body["issue_map"]["intersection"]["verdict"] == "v"

    missing = client.get("/api/issue-map/latest", params={"principal": "Endpoint Probe", "issue": "KRA"})
    assert missing.status_code == 404


def test_datetimes_in_the_map_do_not_break_storage(db_session, monkeypatch):
    """An issue map carries real datetimes — the window, the generated-at
    stamp, every posted_at in the evidence sample — and JSONB refuses them.
    The guard around storage would have swallowed that, so recovery would have
    silently never worked."""
    _use_test_session(monkeypatch, db_session)
    db_session.add(Politician(name="Datetime Probe"))
    db_session.commit()

    api_server._store_issue_map("Datetime Probe", "SHA", {
        "generated_at": datetime(2026, 8, 19, 12, 0),
        "window": {"start": datetime(2026, 1, 1), "end": datetime(2026, 6, 1)},
        "evidence_sample": [{"text": "x", "posted_at": datetime(2026, 3, 3)}],
        "intersection": {"verdict": "survived"},
    })
    recovered = api_server._lookup_issue_map("Datetime Probe", "SHA")
    assert recovered is not None, "storage silently failed on datetimes"
    assert recovered["intersection"]["verdict"] == "survived"
    assert recovered["evidence_sample"][0]["posted_at"].startswith("2026-03-03")


def test_a_storage_failure_never_costs_the_caller_the_map(monkeypatch):
    """The map is the thing the caller waited an hour for."""
    def boom():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api_server, "SessionLocal", boom)
    api_server._store_issue_map("Anyone", "SHA", {"intersection": {}})  # must not raise

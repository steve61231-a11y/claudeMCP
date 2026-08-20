"""A run must outlive the page that started it.

Sections were streamed into process memory only. That tied the result to one
browser sitting on one page: a run that outlived the poll window, a reload, a
closed tab, or a restarted free instance threw away everything already
produced. The work had happened — there was nowhere to look for it.

Progress is now written per section, keyed by SUBJECT rather than by a job id
that only means something to the process that minted it.
"""

from datetime import datetime

from fastapi.testclient import TestClient

from engine import api_server
from engine.db.models import IntelligenceReport, Politician, RunProgress


def _use_test_session(monkeypatch, db_session):
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


def _client(monkeypatch):
    monkeypatch.setattr(api_server, "_require_api_key", lambda key: None)
    monkeypatch.setattr(api_server, "_check_rate_limit", lambda host: None)
    return TestClient(api_server.app)


def test_subject_key_distinguishes_a_report_from_an_issue_map():
    assert api_server._subject_key("William Ruto") == "william ruto"
    assert api_server._subject_key("William Ruto", "SHA") == "william ruto|sha"
    # Whatever the user typed must reach the same row.
    assert api_server._subject_key("  William RUTO ", " sha ") == "william ruto|sha"


def test_progress_is_upserted_in_place_not_appended(db_session, monkeypatch):
    """One row per subject and kind — a run that publishes twenty sections must
    not leave twenty rows behind."""
    _use_test_session(monkeypatch, db_session)
    for i in range(3):
        api_server._save_progress("subject a", "report", status="running",
                                  stage=f"stage {i}", sections_ready=[f"s{i}"],
                                  payload={"n": i})
    rows = db_session.query(RunProgress).filter_by(subject_key="subject a", kind="report").all()
    assert len(rows) == 1
    assert rows[0].stage == "stage 2"
    assert rows[0].payload == {"n": 2}


def test_a_report_and_an_issue_map_for_one_person_do_not_collide(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("ruto", "report", payload={"kind": "report"})
    api_server._save_progress("ruto|sha", "issue_map", payload={"kind": "map"})
    assert api_server._read_progress("ruto", "report")["payload"] == {"kind": "report"}
    assert api_server._read_progress("ruto|sha", "issue_map")["payload"] == {"kind": "map"}


def test_sections_are_readable_while_the_run_is_still_going(db_session, monkeypatch):
    """The whole point: a reader picks the run up mid-flight."""
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("mid flight", "report", status="running",
                              stage="Analyzing 612 mentions…",
                              sections_ready=["sentiment_breakdown", "public_voice"],
                              payload={"name": "Mid Flight", "publicVoice": {"critical": [1]}})
    progress = api_server._read_progress("mid flight", "report")
    assert progress["status"] == "running"
    assert "public_voice" in progress["sections_ready"]
    assert progress["payload"]["publicVoice"]["critical"] == [1]


def test_datetimes_in_a_partial_do_not_break_the_write(db_session, monkeypatch):
    """Partials carry the same real datetimes the finished payload does."""
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("dt subject", "issue_map", status="running",
                              payload={"window": {"start": datetime(2026, 1, 1)},
                                       "intersection": {"verdict": "survived"}})
    progress = api_server._read_progress("dt subject", "issue_map")
    assert progress is not None, "the write silently failed on datetimes"
    assert progress["payload"]["intersection"]["verdict"] == "survived"


def test_a_failure_is_recorded_against_the_subject(db_session, monkeypatch):
    """A reader who comes back later deserves the reason, not an empty screen."""
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("failed subject", "report", status="failed",
                              error="report generation failed: boom")
    progress = api_server._read_progress("failed subject", "report")
    assert progress["status"] == "failed"
    assert "boom" in progress["error"]


def test_a_later_success_clears_an_earlier_failure(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("retried", "report", status="failed", error="boom")
    api_server._save_progress("retried", "report", status="done", payload={"ok": True})
    progress = api_server._read_progress("retried", "report")
    assert progress["status"] == "done"
    assert progress["error"] is None


def test_progress_never_lands_in_the_report_history(db_session, monkeypatch):
    """A half-built payload filed as an IntelligenceReport would be picked up by
    compute_deltas as "the previous report" and silently corrupt every
    subsequent report-over-report comparison."""
    _use_test_session(monkeypatch, db_session)
    subject = Politician(name="Delta Probe")
    db_session.add(subject)
    db_session.commit()

    api_server._save_progress("delta probe", "report", status="running", payload={"half": "built"})
    assert db_session.query(IntelligenceReport).filter_by(politician_id=subject.id).count() == 0


def test_a_progress_write_failure_never_costs_the_run(monkeypatch):
    def boom():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(api_server, "SessionLocal", boom)
    api_server._save_progress("anyone", "report", status="running")  # must not raise
    assert api_server._read_progress("anyone", "report") is None


def test_the_endpoint_serves_a_run_by_subject(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("endpoint subject", "report", status="running",
                              stage="Reading…", sections_ready=["a"], payload={"name": "X"})
    client = _client(monkeypatch)

    resp = client.get("/api/progress", params={"name": "Endpoint Subject"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["stage"] == "Reading…"
    assert body["payload"]["name"] == "X"

    assert client.get("/api/progress", params={"name": "Nobody"}).status_code == 404


def test_the_endpoint_addresses_an_issue_map_by_its_pair(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    api_server._save_progress("ruto|sha", "issue_map", status="done", payload={"intersection": {}})
    client = _client(monkeypatch)
    resp = client.get("/api/progress",
                      params={"name": "Ruto", "kind": "issue_map", "issue": "SHA"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"

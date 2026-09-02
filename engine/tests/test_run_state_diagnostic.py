"""A run must be able to report its own state without the report shaper.

Everything a reader sees about a live run — sections, warnings, the stage
ledger — travels inside a SHAPED report payload. So when shaping is what
fails, the failure cannot be reported: the diagnosis needs the thing that is
broken. A run stuck for sixteen minutes had no way to say why.

/api/admin/run-state reads the raw job and progress row and reports them flat.
"""

import time
from datetime import datetime

import pytest

from engine import api_server, stages


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(api_server.settings, "pulse_api_key", "", raising=False)
    api_server._jobs.clear()
    stages.reset()
    yield
    api_server._jobs.clear()
    stages.reset()


def _state(name="Edwin Sifuna"):
    return api_server.run_state(name=name)


def test_it_answers_even_when_nothing_has_ever_run(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    state = _state()
    assert state["ok"] is True
    assert state["live_job"] is None


def test_it_reports_a_live_job_and_whether_the_page_can_draw_it(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    api_server._jobs["j1"] = {
        "status": "running", "created_at": time.time() - 990,
        "subject": ("edwin sifuna", "politician"),
        "stage": "Read 132 new mentions. Now scoring…",
    }
    live = _state()["live_job"]
    assert live["job_id"] == "j1"
    assert live["age_seconds"] >= 989
    assert live["has_partial"] is False, (
        "this is the whole diagnosis: a running job with no partial means "
        "nothing can reach the browser")
    assert live["sections_ready"] == []


def test_a_healthy_live_job_reports_its_sections(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    api_server._jobs["j2"] = {
        "status": "running", "created_at": time.time(),
        "subject": ("edwin sifuna", "politician"),
        "partial": {"name": "Edwin Sifuna"},
        "sections_ready": ["volume_trends", "sentiment_breakdown"],
    }
    live = _state()["live_job"]
    assert live["has_partial"] is True
    assert live["sections_ready"] == ["volume_trends", "sentiment_breakdown"]


def test_it_carries_the_stage_ledger(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    stages.current().failed("publish_partial", RuntimeError("shaping blew up"))
    state = _state()
    assert "publish_partial" in state["stages"]["failed"]
    assert "shaping blew up" in state["stages"]["stages"][0]["error"]


def test_it_carries_the_stored_progress_row(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: {
        "status": "running", "stage": "Scoring…", "sections_ready": ["volume_trends"],
        "payload": {"volume_trends": {}}, "started_at": str(datetime.utcnow()),
        "updated_at": str(datetime.utcnow()), "error": None})
    stored = _state()["stored_progress"]
    assert stored["sections_ready"] == ["volume_trends"]
    assert stored["has_payload"] is True


def test_it_does_not_call_the_report_shaper(monkeypatch):
    """The point of this endpoint is that it survives the shaper being broken."""
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_build_frontend_payload",
                        lambda *a, **k: pytest.fail("the diagnostic used the broken path"))
    assert _state()["ok"] is True


def test_it_explains_how_to_read_itself(monkeypatch):
    monkeypatch.setattr(api_server, "_read_progress", lambda *a, **k: None)
    reading = _state()["reading"]
    assert "sections_ready empty" in reading
    assert "has_partial false" in reading

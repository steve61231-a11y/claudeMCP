"""A run the engine lost is said in seconds, not after thirty minutes of spinner.

On 10 Nov 2026 the engine restarted while a report was being built. The run
died with the process; its stored row kept saying "running", and the only
test for a dead run was thirty minutes of silence — so the page showed
"Still building this report · 0/16 sections" about a run nobody was running.
A live run now touches its row every few seconds from its own thread
(`_start_run`), and a row not touched since before this process started, or
for ninety seconds, is reported as stalled with the reason.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from engine import api_server


def _row(db_session, job_id, updated):
    from engine.db.models import RunProgress

    row = RunProgress(subject_key="martha karua", kind="report", job_id=job_id, status="running",
                      stage="Scoring…", started_at=updated, updated_at=updated)
    db_session.add(row)
    db_session.commit()


def _patch(monkeypatch, db_session):
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


def test_a_run_from_before_this_process_started_is_stalled_by_a_restart(db_session, monkeypatch):
    _patch(monkeypatch, db_session)
    _row(db_session, "old-job", api_server._BOOT_TIME - timedelta(seconds=20))
    got = api_server._read_progress("martha karua", "report")
    assert got["status"] == "stalled" and got["stalled_reason"] == "engine_restarted"


def test_a_run_this_process_holds_and_hears_from_is_running(db_session, monkeypatch):
    _patch(monkeypatch, db_session)
    monkeypatch.setitem(api_server._jobs, "live-job", {"status": "running", "created_at": time.time()})
    _row(db_session, "live-job", datetime.utcnow())
    assert api_server._read_progress("martha karua", "report")["status"] == "running"


def test_a_held_run_silent_past_the_heartbeat_is_stalled(db_session, monkeypatch):
    _patch(monkeypatch, db_session)
    monkeypatch.setitem(api_server._jobs, "hung-job", {"status": "running", "created_at": time.time()})
    _row(db_session, "hung-job", datetime.utcnow() - timedelta(seconds=api_server._RUN_DEAD_AFTER_SECONDS + 30))
    got = api_server._read_progress("martha karua", "report")
    assert got["status"] == "stalled" and got["stalled_reason"] == "stopped_reporting"


def test_the_heartbeat_follows_the_worker(monkeypatch):
    beats = []
    monkeypatch.setattr(api_server, "_RUN_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(api_server, "_touch_progress", lambda job_id: beats.append(job_id))
    monkeypatch.setitem(api_server._jobs, "beat-job", {"status": "running", "created_at": time.time()})
    worker = api_server._start_run("beat-job", time.sleep, (0.4,))
    worker.join()
    time.sleep(0.15)
    n = len(beats)
    assert n >= 3, "a live worker must keep its row fresh"
    time.sleep(0.2)
    assert len(beats) == n, "a finished worker must stop beating"


def test_health_counts_runs_in_flight_without_naming_them(monkeypatch):
    monkeypatch.setitem(api_server._jobs, "x", {"status": "running", "subject": ("someone", "politician")})
    assert api_server._runs_in_flight() >= 1


def test_the_page_follows_a_lost_run_and_starts_it_again_once():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "async function followRun(" in page
    assert page.count("restartedNote(end,") == 2, "both the report and the map restart a lost run"
    assert "restarts<1" in page, "restarted once, never in a loop"

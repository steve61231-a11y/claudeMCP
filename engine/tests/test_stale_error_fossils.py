"""A failure must not outlive the run that produced it.

Progress is stored per subject, so a failure recorded by one run sits in that
row until something overwrites it. When the NEXT run outlasted the poll window,
the page fell back to stored progress and presented that fossil as the current
outcome — old message, old stack trace, from a build no longer deployed.

The effect was worse than a stale message: a bug that had already been fixed
looked unfixed, so the fix got debugged instead of the run.
"""

import re
import time
from pathlib import Path

from engine import api_server

APP_HTML = Path(__file__).resolve().parents[2] / "web" / "pulse_app.html"


def _use_test_session(monkeypatch, db_session):
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)


def test_starting_a_run_clears_the_previous_run_s_failure(db_session, monkeypatch):
    _use_test_session(monkeypatch, db_session)
    key = api_server._subject_key("Fossil Probe")

    api_server._save_progress(key, "report", job_id="old-job", status="failed",
                              error="report generation failed: something from a dead build")
    assert api_server._read_progress(key, "report")["error"]

    # A new run claims the row before doing any work.
    api_server._save_progress(key, "report", job_id="new-job",
                              status="running", stage="Starting…", error=None)

    progress = api_server._read_progress(key, "report")
    assert progress["error"] is None
    assert progress["status"] == "running"
    assert progress["job_id"] == "new-job"


def test_progress_says_which_run_it_describes(db_session, monkeypatch):
    """Without the job id there is no way to tell a live outcome from a
    leftover one."""
    _use_test_session(monkeypatch, db_session)
    key = api_server._subject_key("Attribution Probe")
    api_server._save_progress(key, "report", job_id="job-abc", status="failed", error="boom")
    assert api_server._read_progress(key, "report")["job_id"] == "job-abc"


def test_the_report_job_claims_the_row_before_any_work(db_session, monkeypatch):
    """The clear has to happen at the very start. Doing it only on failure
    leaves the fossil visible for the whole of a long run — which is exactly
    the window in which the page falls back to stored progress."""
    _use_test_session(monkeypatch, db_session)
    key = api_server._subject_key("Ordering Probe")
    api_server._save_progress(key, "report", job_id="old", status="failed", error="ancient")

    # Make the job bail immediately after the claim.
    monkeypatch.setattr(api_server.settings, "serve_precache_first", True, raising=False)
    monkeypatch.setattr(api_server, "_lookup_precache", lambda name: {"name": "cached"})
    api_server._jobs["ordering-job"] = {"status": "running", "created_at": time.time()}
    try:
        api_server._run_report_job("ordering-job", "Ordering Probe")
    finally:
        api_server._jobs.pop("ordering-job", None)

    progress = api_server._read_progress(key, "report")
    assert progress["error"] is None, "the fossil survived into a new run"
    assert progress["job_id"] == "ordering-job"


def test_the_ui_refuses_to_show_another_run_s_error():
    """The guard has to exist in the page, not just in the backend: the page is
    what decides whether a stored error is presented as this run's outcome."""
    html = APP_HTML.read_text(encoding="utf-8")
    assert "errorBelongsToRun" in html
    # It must compare against the current job, not merely check for a message.
    guard = re.search(r"const errorBelongsToRun = \(prog, job_id\) =>\n(.*?);\n", html, re.S)
    assert guard, "guard not found in its expected form"
    body = guard.group(1)
    assert "prog.job_id === job_id" in body
    assert "'failed'" in body
    # And every place that renders a stored error must go through it.
    assert "prog&&prog.error){" not in html, "a raw stored-error render survived"

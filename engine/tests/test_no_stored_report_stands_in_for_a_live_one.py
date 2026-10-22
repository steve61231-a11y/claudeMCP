"""No stored report may stand in for the one the reader asked for.

The engine kept two pre-generated reports — "Mbadi" (December 2025 – July
2026) and "Edwin Sifuna" (December 2025 – June 2026) — and when a live run
FAILED for any name containing either word, it served the stored one,
marked `ok: True`, with a small "stale fallback" pill as the only sign. A
reader who asked for this month got a report about another period, written
from another corpus, presented as the answer.

That is the fake data the founder ruled out on 22 October 2026 ("If it's 1
month, we do 1 month … We don't want fake data"), and Edwin Sifuna was one of
the two subjects named for testing that day. It also pre-empted
`_partial_payload`, which hands over what the failed run DID produce under a
banner saying it is incomplete — the founding rule at the scale of a run.
"""

import time

from engine import api_server
from engine.config import settings


def test_a_failed_run_is_a_failure_whatever_the_name(monkeypatch, db_session):
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(api_server, "_set_stage", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_ensure_politician",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the run died")))
    job_id = "sifuna-fails"
    api_server._jobs[job_id] = {"status": "running", "created_at": time.time()}
    try:
        api_server._run_report_job(job_id, "Edwin Sifuna")
        job = api_server._jobs[job_id]
    finally:
        api_server._jobs.pop(job_id, None)

    assert job["status"] == "done"
    assert not job.get("stale_fallback"), "a stored report was served in place of this run"
    report = job.get("report") or {}
    assert "2025-12-01" not in str(report.get("window", "")), \
        "a report about another period was delivered as this one"
    if job.get("ok"):
        # Only what this run produced may be delivered, and it must say so.
        assert report.get("partial_failure"), "a failed run delivered as a complete one"
    else:
        assert "the run died" in job.get("error", "")


def test_there_is_no_switch_to_serve_stored_reports():
    """An operator who finds an old SERVE_PRECACHE_FIRST in a host's variables
    must not be able to believe it still does something."""
    assert not hasattr(settings, "serve_precache_first")
    assert not hasattr(api_server, "_lookup_precache")

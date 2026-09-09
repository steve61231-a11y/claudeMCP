"""A run that died must not block every run after it.

From a live session: "Still working on Martha Karua… Nothing has arrived for
587 minutes, and that is not normal." Pressing Generate again answered
"Already running — a run for this subject was already in progress, so this
reconnected to it instead of starting a second one." The subject was locked by
a run that had been dead for ten hours, and there was no way to start another.

Two decisions combined to produce it, each defensible alone:

  - `_evict_stale_jobs` never evicts a RUNNING job however old, because a
    full-stack issue map can legitimately outlive an hour;
  - `_inflight_job` returns any job whose status is "running", to stop a
    second pipeline racing the first over the same rate-limited API.

Neither asks whether the job is still ALIVE. A thread killed by a deploy, a
container restart, an OOM or an exception outside the guarded block leaves
status "running" forever. Age is the wrong test; silence is the right one.
"""

import time

import pytest

from engine import api_server


@pytest.fixture(autouse=True)
def _clean_jobs():
    api_server._jobs.clear()
    yield
    api_server._jobs.clear()


def _job(subject, silent_for):
    api_server._jobs["dead"] = {
        "status": "running",
        "subject": subject,
        "created_at": time.time() - silent_for,
        "last_update": time.time() - silent_for,
    }


def test_a_run_that_went_silent_stops_blocking_new_runs():
    _job(("martha karua", "politician"), api_server._JOB_SILENCE_SECONDS + 60)
    assert api_server._inflight_job("Martha Karua", "politician") is None


def test_the_dead_run_is_marked_finished_not_left_running():
    """Left "running", it would go on being counted as in flight forever."""
    _job(("martha karua", "politician"), api_server._JOB_SILENCE_SECONDS + 60)
    api_server._inflight_job("Martha Karua", "politician")
    assert api_server._jobs["dead"]["status"] == "done"
    assert api_server._jobs["dead"]["ok"] is False
    assert "abandoned" in api_server._jobs["dead"]["error"]


def test_a_working_run_is_still_reattached_to():
    """The dedupe exists for a real reason: a second pipeline racing the first
    over the same rate-limited API made both slower than one."""
    _job(("martha karua", "politician"), 30)
    assert api_server._inflight_job("Martha Karua", "politician") == "dead"


def test_a_slow_but_live_run_is_not_killed_for_being_old():
    """A full issue map can outlive an hour. What matters is whether it is
    still producing, not how long it has been going."""
    api_server._jobs["slow"] = {
        "status": "running",
        "subject": ("martha karua", "politician"),
        "created_at": time.time() - 7200,      # two hours old
        "last_update": time.time() - 20,       # but talking
    }
    assert api_server._inflight_job("Martha Karua", "politician") == "slow"


def test_a_job_that_never_reported_falls_back_to_its_start_time():
    """No heartbeat yet is not a licence to run forever."""
    api_server._jobs["mute"] = {
        "status": "running",
        "subject": ("martha karua", "politician"),
        "created_at": time.time() - (api_server._JOB_SILENCE_SECONDS + 60),
    }
    assert api_server._inflight_job("Martha Karua", "politician") is None


def test_silence_is_shorter_than_a_run_but_longer_than_a_stage():
    """Far beyond the slowest legitimate stage, nowhere near the hours a real
    run can take."""
    from engine.config import settings

    assert api_server._JOB_SILENCE_SECONDS > settings.analyst_deadline_seconds
    assert api_server._JOB_SILENCE_SECONDS < api_server._JOB_TTL_SECONDS * 2

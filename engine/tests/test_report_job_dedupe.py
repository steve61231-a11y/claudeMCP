"""One run per subject at a time.

A slow run invites the user to click Generate again. That used to launch a
second pipeline over the same rate-limited API and the same rows: both ran
slower than one would have, so the wait that prompted the click got worse. On a
free provider — serialised, several seconds per call — this is the difference
between a report finishing and a report never finishing.
"""

import time

from fastapi.testclient import TestClient

from engine import api_server


def _client(monkeypatch):
    started = []

    class _Thread:
        def __init__(self, target=None, args=(), daemon=None):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(api_server.threading, "Thread", _Thread)
    monkeypatch.setattr(api_server, "_require_api_key", lambda key: None)
    monkeypatch.setattr(api_server, "_check_rate_limit", lambda host: None)
    api_server._jobs.clear()
    return TestClient(api_server.app), started


def test_second_request_reattaches_instead_of_starting_a_rival_run(monkeypatch):
    client, started = _client(monkeypatch)

    first = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()
    second = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()

    assert second["job_id"] == first["job_id"], "started a competing pipeline"
    assert second["already_running"] is True
    assert len(started) == 1


def test_matching_ignores_case_and_padding(monkeypatch):
    """The same subject typed differently is still the same subject."""
    client, started = _client(monkeypatch)

    first = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()
    second = client.post("/api/report", json={"name": "  edwin sifuna ", "type": "weekly"}).json()

    assert second["job_id"] == first["job_id"]
    assert len(started) == 1


def test_a_different_subject_still_starts_its_own_run(monkeypatch):
    client, started = _client(monkeypatch)

    first = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()
    second = client.post("/api/report", json={"name": "John Mbadi", "type": "weekly"}).json()

    assert second["job_id"] != first["job_id"]
    assert len(started) == 2


def test_a_finished_run_does_not_block_a_fresh_one(monkeypatch):
    """Re-attaching is for runs in flight only — a completed report must never
    stop the user asking for an up-to-date one."""
    client, started = _client(monkeypatch)

    first = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()
    api_server._jobs[first["job_id"]].update({"status": "done", "ok": True,
                                              "created_at": time.time()})

    second = client.post("/api/report", json={"name": "Edwin Sifuna", "type": "weekly"}).json()

    assert second["job_id"] != first["job_id"]
    assert len(started) == 2

"""The live server says which primary-record hosts it can reach.

GitHub's network reaches Hansard, Kenya Law's cause lists and gazettes,
Parliament and the World Bank (scripts/source_probe.py, 2 Nov 2026). Railway's
address is a different datacenter and Kenya Law refused it before; a
connector built on a host the server cannot reach would report "no court
record" for someone who has one. So /api/health reports the server's own
reach — public hosts, no subject — measured in the background so the health
check never waits, and deploy-drift prints it.
"""

from pathlib import Path

from engine.evidence import record_reach


def test_reach_is_measured_in_the_background_and_cached(monkeypatch):
    monkeypatch.setattr(record_reach, "ENABLED", True)
    monkeypatch.setattr(record_reach, "_state", {"measured_at": None, "hosts": {}, "running": False})
    started = []

    class Thread:
        def __init__(self, target, daemon):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(record_reach.threading, "Thread", Thread)
    assert record_reach.reach()["state"] == "measuring"
    assert record_reach.reach()["state"] == "measuring" and len(started) == 1, "one measurement at a time"

    monkeypatch.setattr(record_reach, "_check", lambda url: {"ok": True, "status": 200, "bytes": 999})
    record_reach._measure()
    out = record_reach.reach()
    assert out["state"] == "measured" and set(out["hosts"]) == set(record_reach.HOSTS)


def test_no_host_names_a_subject():
    for url in record_reach.HOSTS.values():
        assert "ruto" not in url.lower() and "q=" not in url.split("?")[-1].lower()


def test_health_and_the_drift_check_carry_it():
    root = Path(__file__).resolve().parents[2]
    assert '"record_reach": _record_reach()' in (root / "engine" / "api_server.py").read_text()
    assert "Record hosts from the live server" in (root / ".github" / "workflows" / "deploy-drift.yml").read_text()

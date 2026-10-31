"""Railway's only word on it was an email: "Your deployment for claudeMCP ran
out of memory within the production environment and crashed." No stage, no
figure, no limit — and a crash of that kind kills every run in flight and
leaves nothing in the ledger, so each looked like a report still thinking.

The engine now reads its own memory and its container's limit, reports both
at /api/health, records the figure at every stage lap, and refuses NEW work
near the limit with a sentence rather than crashing the runs in progress. An
unreadable figure is None, never zero, and never a refusal.
"""

from pathlib import Path

import pytest

from engine import memory


def test_the_limit_is_read_from_the_cgroup_and_no_limit_is_none(tmp_path):
    v2 = tmp_path / "memory.max"
    v2.write_text("1073741824\n")
    assert memory.limit_mb((str(v2),)) == 1024.0
    v2.write_text("max\n")
    assert memory.limit_mb((str(v2),)) is None
    v1 = tmp_path / "limit_in_bytes"
    v1.write_text(str(9223372036854771712))
    assert memory.limit_mb((str(v1),)) is None, "v1's 'unlimited' is the host's number, not a limit"
    assert memory.limit_mb((str(tmp_path / "absent"),)) is None


def test_this_process_can_read_its_own_memory():
    snap = memory.snapshot()
    assert snap["rss_mb"] and snap["rss_mb"] > 0
    assert snap["peak_mb"] >= snap["rss_mb"]


@pytest.mark.parametrize("rss,limit,refused", [
    (900, 1024, True), (500, 1024, False), (900, None, False), (None, 1024, False)])
def test_new_work_is_refused_only_near_a_limit_both_figures_confirm(monkeypatch, rss, limit, refused):
    monkeypatch.setattr(memory, "rss_mb", lambda: rss)
    monkeypatch.setattr(memory, "limit_mb", lambda files=None: limit)
    reason = memory.refusal()
    assert bool(reason) is refused
    if reason:
        assert "900 MB of its 1024 MB" in reason and "Try again" in reason


def test_every_stage_lap_records_memory():
    from engine.stages import StageLedger

    ledger = StageLedger()
    ledger.lap("collection")
    assert ledger.summary()["memory_mb"]["collection"] > 0


def test_a_run_near_the_limit_is_refused_with_a_sentence(monkeypatch):
    from fastapi import HTTPException

    from engine import api_server

    monkeypatch.setattr(memory, "refusal", lambda: "The engine is using 950 MB of its 1024 MB memory limit")
    with pytest.raises(HTTPException) as caught:
        api_server._admit_or_refuse()
    assert caught.value.status_code == 503 and "950 MB" in caught.value.detail


def test_the_wiring_is_where_it_must_be():
    root = Path(__file__).resolve().parents[2]
    src = (root / "engine" / "api_server.py").read_text()
    assert src.count("_admit_or_refuse()") >= 3, "both run endpoints call it, beside its definition"
    assert '"memory": _memory_snapshot()' in src
    assert "not started near the memory limit" in src, "the back office waits near the limit"
    assert 'MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}"' in (root / "engine" / "start.sh").read_text()
    assert "ss.memory_mb" in (root / "web" / "pulse_app.html").read_text()

"""Sections must reach the reader as they are made, not all at the end.

A full report takes tens of minutes. Until now the job held everything back
until the last stage finished, so depth and a bearable wait looked like a
trade-off. They aren't — the sections just have to be published as they land.
"""

import time
from datetime import datetime, timedelta

from engine import api_server
from engine.reports import sections


def _base_payload() -> dict:
    return {
        "executive_summary": "seed",
        "sentiment_breakdown": {"positive_pct": 30, "neutral_pct": 40, "negative_pct": 30,
                                "total_mentions_analyzed": 10},
        "volume_trends": {"total_mentions": 10, "by_platform": {"news": 10}, "by_day": {}},
        "influence_summary": [],
        "narrative_breakdown": [],
    }


def test_each_section_is_published_the_moment_it_lands(monkeypatch):
    monkeypatch.setattr(sections, "generate_executive_summary", lambda c: "summary")
    monkeypatch.setattr(sections, "generate_risks", lambda c: ["r"])
    monkeypatch.setattr(sections, "generate_opportunities", lambda c: ["o"])
    monkeypatch.setattr(sections, "generate_trends", lambda c: ["t"])

    published: list[tuple[str, object]] = []
    payload = sections.enrich_report_payload(
        "Subject", datetime(2026, 1, 1), datetime(2026, 2, 1), _base_payload(),
        on_section=lambda k, v: published.append((k, v)),
    )

    assert dict(published) == {"executive_summary": "summary", "risks": ["r"],
                               "opportunities": ["o"], "trends": ["t"]}
    assert payload["risks"] == ["r"]


def test_a_broken_reader_never_costs_a_section(monkeypatch):
    """Streaming is a courtesy. If the callback throws, the report still runs."""
    monkeypatch.setattr(sections, "generate_executive_summary", lambda c: "summary")
    monkeypatch.setattr(sections, "generate_risks", lambda c: ["r"])
    monkeypatch.setattr(sections, "generate_opportunities", lambda c: [])
    monkeypatch.setattr(sections, "generate_trends", lambda c: [])

    def boom(key, value):
        raise RuntimeError("reader went away")

    payload = sections.enrich_report_payload(
        "Subject", datetime(2026, 1, 1), datetime(2026, 2, 1), _base_payload(), on_section=boom,
    )
    assert payload["risks"] == ["r"]
    assert payload["executive_summary"] == "summary"


def test_a_slow_section_does_not_hold_back_a_fast_one(monkeypatch):
    """pool.map yields in submission order, so a five-second section used to
    wait behind a four-minute one. Nothing downstream depends on the order."""
    monkeypatch.setattr(sections, "generate_executive_summary",
                        lambda c: (time.sleep(0.35), "slow")[1])
    monkeypatch.setattr(sections, "generate_risks", lambda c: ["fast"])
    monkeypatch.setattr(sections, "generate_opportunities", lambda c: [])
    monkeypatch.setattr(sections, "generate_trends", lambda c: [])

    order: list[str] = []
    sections.enrich_report_payload(
        "Subject", datetime(2026, 1, 1), datetime(2026, 2, 1), _base_payload(),
        on_section=lambda k, v: order.append(k),
    )
    assert order.index("risks") < order.index("executive_summary")


class _Politician:
    id = 1
    name = "Subject"


def test_partial_report_appears_on_the_job_while_it_runs(monkeypatch):
    job_id = "job-under-test"
    api_server._jobs[job_id] = {"status": "running", "created_at": time.time()}
    monkeypatch.setattr(api_server, "_build_frontend_payload",
                        lambda pol, rep: {"name": pol.name, "keys": sorted(rep.payload)})
    ws, we = datetime(2026, 1, 1), datetime(2026, 2, 1)
    try:
        # Nothing to shape yet — the base statistics haven't landed.
        api_server._publish_partial(job_id, _Politician(), {"risks": []}, ws, we)
        assert "partial" not in api_server._jobs[job_id]

        payload = _base_payload()
        api_server._publish_partial(job_id, _Politician(), payload, ws, we)
        assert api_server._jobs[job_id]["partial"]["name"] == "Subject"
        first = api_server._jobs[job_id]["sections_ready"]

        payload["public_voice"] = {"critical": [{"theme": "t"}]}
        # Sections arrive minutes apart on a real run; the burst debounce only
        # collapses publishes that land in the same second.
        api_server._jobs[job_id]["partial_at"] = 0.0
        api_server._publish_partial(job_id, _Politician(), payload, ws, we)
        assert "public_voice" in api_server._jobs[job_id]["sections_ready"]
        assert len(api_server._jobs[job_id]["sections_ready"]) > len(first)
    finally:
        api_server._jobs.pop(job_id, None)


def test_a_burst_of_sections_costs_one_rebuild(monkeypatch):
    """The base statistics land as a dozen keys at once. Shaping each of them
    separately would be a dozen round-trips describing identical data."""
    job_id = "job-burst"
    api_server._jobs[job_id] = {"status": "running", "created_at": time.time()}
    builds: list[int] = []
    monkeypatch.setattr(api_server, "_build_frontend_payload",
                        lambda pol, rep: (builds.append(1), {"name": pol.name})[1])
    ws, we = datetime(2026, 1, 1), datetime(2026, 2, 1)
    try:
        payload = _base_payload()
        for key in ("a", "b", "c", "d", "e"):
            payload[key] = key
            api_server._publish_partial(job_id, _Politician(), payload, ws, we)
        assert len(builds) == 1
        assert api_server._jobs[job_id]["partial"]["name"] == "Subject"
    finally:
        api_server._jobs.pop(job_id, None)


def test_partial_publishing_stops_once_the_job_is_done():
    """A finished job carries the real report; a late partial must not
    overwrite or shadow it."""
    job_id = "job-finished"
    api_server._jobs[job_id] = {"status": "done", "ok": True, "report": {"real": True},
                                "created_at": time.time()}
    try:
        api_server._publish_partial(job_id, _Politician(), _base_payload(),
                                    datetime(2026, 1, 1), datetime(2026, 2, 1))
        assert "partial" not in api_server._jobs[job_id]
        assert api_server._jobs[job_id]["report"] == {"real": True}
    finally:
        api_server._jobs.pop(job_id, None)


def test_a_half_built_payload_never_breaks_the_run(monkeypatch):
    job_id = "job-broken-shape"
    api_server._jobs[job_id] = {"status": "running", "created_at": time.time()}

    def boom(pol, rep):
        raise KeyError("influence_summary")

    monkeypatch.setattr(api_server, "_build_frontend_payload", boom)
    try:
        api_server._publish_partial(job_id, _Politician(), _base_payload(),
                                    datetime(2026, 1, 1), datetime(2026, 2, 1))
        assert "partial" not in api_server._jobs[job_id]
    finally:
        api_server._jobs.pop(job_id, None)


def test_window_travels_with_the_partial():
    """The frontend header reads window_start/window_end off the report row,
    which doesn't exist yet mid-run."""
    ws = datetime(2026, 1, 1)
    we = ws + timedelta(days=30)
    stub = api_server._PartialReport({"a": 1}, ws, we)
    assert stub.window_start == ws and stub.window_end == we and stub.payload == {"a": 1}

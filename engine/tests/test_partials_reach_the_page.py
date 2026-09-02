"""A live run must show its sections while it runs.

Symptom: the stage text advanced ("Read 110 new mentions…") for an hour and
not one section ever appeared. The pipeline was fine — traced end to end it
publishes its first section at 0.0s and all 43 within 15 seconds.

The break was at the seam. `_build_frontend_payload` read payload keys with
bracket access, so a HALF-BUILT payload raised KeyError; `_publish_partial`
caught every exception and returned; nothing was saved and nothing said so. A
reader watching a working run saw a spinner and no output.
"""

from datetime import datetime, timedelta

import pytest

from engine import stages
from engine.api_server import _PartialReport, _build_frontend_payload
from engine.db.models import Politician


@pytest.fixture()
def subject(db_session):
    politician = Politician(name="Partial Subject", aliases=[], titles=[],
                            swahili_terms=[], subject_type="politician")
    db_session.add(politician)
    db_session.commit()
    return politician


@pytest.fixture(autouse=True)
def _use_the_test_database(db_session, monkeypatch):
    """The builder opens its own session; point it at the test database."""
    from engine import api_server

    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)


def _shape(subject, payload):
    now = datetime.utcnow()
    return _build_frontend_payload(
        subject, _PartialReport(dict(payload), now - timedelta(days=30), now))


# --- every stage of a growing payload must shape --------------------------

def test_a_completely_empty_payload_shapes(subject):
    assert _shape(subject, {})


def test_each_key_of_the_preview_shapes_as_it_arrives(subject):
    """The pipeline publishes one key at a time, so the builder is handed the
    payload seven times on the way to a complete preview. Every one of those
    used to raise."""
    from engine.reports.generator import corpus_preview

    preview = corpus_preview([], datetime.utcnow())
    live = {}
    for key, value in preview.items():
        live[key] = value
        assert _shape(subject, live), f"shaping failed once {key} had arrived"


def test_keys_present_but_null_do_not_raise(subject):
    assert _shape(subject, {"sentiment_breakdown": None, "volume_trends": None,
                            "influence_summary": None, "narrative_breakdown": None,
                            "executive_summary": None})


def test_a_partial_sentiment_block_does_not_raise(subject):
    """corpus_preview sends percentages as None — "not yet known" — and a later
    stage fills them in. Reading them with brackets before then raised."""
    assert _shape(subject, {"sentiment_breakdown": {"total_mentions_analyzed": 0}})


def test_the_volume_numbers_survive_to_the_page(subject):
    shaped = _shape(subject, {"volume_trends": {"total_mentions": 110,
                                                "by_platform": {"x": 110}, "by_day": {}}})
    assert shaped["volume"]["total"] == 110


# --- and a failure must never be silent again -------------------------------

def test_a_failed_partial_publish_is_recorded(monkeypatch, subject):
    from engine import api_server

    stages.reset()

    def boom(*a, **k):
        raise RuntimeError("something unforeseen")

    monkeypatch.setattr(api_server, "_build_frontend_payload", boom)
    job: dict = {"status": "running"}
    monkeypatch.setitem(api_server._jobs, "j1", job)
    api_server._publish_partial("j1", subject, {"volume_trends": {}},
                                datetime.utcnow() - timedelta(days=30), datetime.utcnow())
    assert any(r.name == "publish_partial" for r in stages.current().failures), (
        "a publish that fails silently is why an hour-long run showed nothing")
    stages.reset()


def test_the_builder_uses_no_bare_bracket_reads_on_the_payload():
    """A new payload[...] here reintroduces exactly this bug: it raises on a
    partial, gets swallowed, and the page goes quiet for the whole run."""
    import inspect

    from engine import api_server

    source = inspect.getsource(api_server._build_frontend_payload)
    offenders = [line.strip() for line in source.splitlines()
                 if 'payload["' in line or 'sentiment["' in line or 'volume["' in line]
    assert not offenders, f"bracket reads on a partial payload: {offenders}"


def test_publishing_no_longer_waits_for_two_particular_keys(monkeypatch, subject):
    """Sections were withheld until sentiment_breakdown AND volume_trends both
    existed — a gate that only made sense while the shaper could not survive a
    partial. It delayed the first thing a reader sees for no benefit."""
    from engine import api_server

    saved = {}
    monkeypatch.setattr(api_server, "_save_progress",
                        lambda *a, **k: saved.update(k))
    job = {"status": "running"}
    monkeypatch.setitem(api_server._jobs, "j2", job)
    api_server._publish_partial("j2", subject, {"executive_summary": "an early line"},
                                datetime.utcnow() - timedelta(days=30), datetime.utcnow())
    assert job.get("sections_ready") == ["executive_summary"], (
        "a section that has landed must reach the page immediately")


# --- a failure in the first seconds must be visible in the first seconds ----

def test_the_ledger_is_published_early_not_only_at_the_end():
    """section_status was stamped on only at the END of a run, so a failure in
    the opening seconds — preflight, the preview, a publish that could not be
    shaped — stayed invisible for the whole run. The reader saw a spinner and
    no explanation, which is the state where a working run and a dead one look
    the same."""
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline.run_analysis)
    publishes = [i for i, line in enumerate(source.splitlines())
                 if 'publish("section_status"' in line]
    assert len(publishes) >= 2, "the ledger is only published once, at the end"

    fanout = next(i for i, line in enumerate(source.splitlines())
                  if "payload = enrich_report_payload(" in line)
    assert publishes[0] < fanout, (
        "the first ledger publish is after the analysts, so anything that failed "
        "before them is invisible while the reader waits")


def test_run_health_is_published_before_the_long_stretch():
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline.run_analysis)
    health_at = source.index('publish("run_health"')
    fanout_at = source.index("payload = enrich_report_payload(")
    assert health_at < fanout_at

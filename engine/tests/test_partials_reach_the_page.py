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





# --- a placeholder must never tick the checklist ----------------------------
#
# Symptom: "Sentiment & volume" showed a green check and "finished and safe
# to read" while every number on the card was "—". corpus_preview() publishes
# sentiment_breakdown early as {"positive_pct": None, ..., "total_mentions_
# analyzed": 0} — a documented placeholder, so a reader isn't staring at
# nothing while the real analysis runs. But it is a non-empty DICT, and the
# old readiness check (`v not in (None, [], {}, "")`) only rejects an empty
# container, not one whose every value inside is a placeholder — so the
# checklist ticked the instant the preview landed, long before the real,
# scored sentiment_breakdown was published later in the run. When the model
# answered in a second or two nobody noticed the gap; once it got slow, the
# gap was minutes and the report looked broken.

def test_a_placeholder_dict_does_not_count_as_ready():
    from engine.api_server import _has_real_content

    placeholder = {"positive_pct": None, "neutral_pct": None,
                   "negative_pct": None, "total_mentions_analyzed": 0}
    assert not _has_real_content(placeholder)


def test_a_dict_with_one_real_value_counts_as_ready():
    from engine.api_server import _has_real_content

    assert _has_real_content({"total_mentions": 110, "by_platform": {}})


def test_the_preview_sentiment_block_is_never_marked_ready():
    """The exact object corpus_preview publishes."""
    from engine.api_server import _has_real_content
    from engine.reports.generator import corpus_preview

    preview = corpus_preview([], datetime.utcnow())
    assert not _has_real_content(preview["sentiment_breakdown"])


def test_the_preview_volume_block_with_real_mentions_is_marked_ready():
    """volume_trends is NOT a placeholder in the preview — it holds real
    platform/day counts from rows already in the database, and correctly
    ticks the checklist immediately. Only sentiment_breakdown is fake."""
    from engine.api_server import _has_real_content
    from engine.reports.generator import corpus_preview

    mentions = [{"id": "m1", "platform": "x", "posted_at": datetime.utcnow(),
                "source_type": "post", "language": "en", "author_handle": "@a",
                "text": "t", "engagement": {}}]
    preview = corpus_preview(mentions, datetime.utcnow())
    assert _has_real_content(preview["volume_trends"])


def test_publish_partial_does_not_mark_sentiment_ready_from_the_preview_alone(
        monkeypatch, subject):
    """End to end through _publish_partial: the exact call the pipeline makes
    when corpus_preview is the only thing published so far."""
    from engine import api_server
    from engine.reports.generator import corpus_preview

    job_id = "job-partial-readiness"
    api_server._jobs[job_id] = {"status": "running"}
    monkeypatch.setattr(api_server, "_save_progress", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_PARTIAL_MIN_INTERVAL_SECONDS", 0)

    preview = corpus_preview([], datetime.utcnow())
    now = datetime.utcnow()
    api_server._publish_partial(job_id, subject, preview,
                                now - timedelta(days=30), now)

    ready = api_server._jobs[job_id]["sections_ready"]
    assert "sentiment_breakdown" not in ready, \
        "the checklist ticked on the placeholder, before any real sentiment existed"


def test_publish_partial_marks_sentiment_ready_once_it_is_real(monkeypatch, subject):
    from engine import api_server

    job_id = "job-partial-readiness-2"
    api_server._jobs[job_id] = {"status": "running"}
    monkeypatch.setattr(api_server, "_save_progress", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_PARTIAL_MIN_INTERVAL_SECONDS", 0)

    now = datetime.utcnow()
    api_server._publish_partial(
        job_id, subject,
        {"sentiment_breakdown": {"positive_pct": 40.0, "neutral_pct": 35.0,
                                 "negative_pct": 25.0, "total_mentions_analyzed": 30}},
        now - timedelta(days=30), now)

    assert "sentiment_breakdown" in api_server._jobs[job_id]["sections_ready"]



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

"""Sentiment must never render as "Positive —, Negative —" for a whole corpus
just because the model failed every call — and the fix for that must never
touch what gets persisted, or a mention scored by keyword-guess would be
permanently blocked from ever being re-scored by a real model.

Three things pinned here:
  - score_items keeps its original contract: an unscored item is left OUT,
    not guessed at, so the database only ever holds a real read.
  - score_items_with_floor fills the gap for a caller that wants a reading
    for every item right now, clearly marked as a lexicon guess.
  - the whole scoring stage has a total wall-clock deadline, so a model that
    fails most calls but occasionally succeeds (never tripping the circuit
    breaker, since any success resets it) cannot run unbounded.
"""
import time

from engine.agents import score as score_agent
from engine.processing.sentiment import lexicon_sentiment


def _items(n):
    return [(f"id-{i}", f"item text number {i}") for i in range(n)]


def test_score_items_still_leaves_unscored_items_out(monkeypatch):
    """The persisted-data contract must not change."""
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", boom)
    assert score_agent.score_items("Subject", _items(10)) == {}


def test_score_items_with_floor_fills_every_gap(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", boom)
    report: dict = {}
    scored = score_agent.score_items_with_floor("Subject", _items(10), report=report)

    assert len(scored) == 10
    assert all(v["source"] == "lexicon" for v in scored.values())
    assert report["scored_by_model"] == 0
    assert report["scored_by_lexicon"] == 10


def test_a_real_score_is_never_overwritten_by_the_floor(monkeypatch):
    def fake_batch(subject, batch, failures=None):
        item_id, _ = batch[0]
        return {item_id: {"sentiment": "positive", "intensity": 4, "context_tag": None,
                          "topic": "t", "language": "en", "confidence": 0.9,
                          "source": "llm_batch"}}

    monkeypatch.setattr(score_agent, "_score_batch", fake_batch)
    scored = score_agent.score_items_with_floor("Subject", _items(1))
    assert scored["id-0"]["source"] == "llm_batch"
    assert scored["id-0"]["sentiment"] == "positive"


def test_lexicon_sentiment_never_claims_to_be_a_model_reading():
    reading = lexicon_sentiment("The president delivered a great success for the country")
    assert reading["source"] == "lexicon"
    assert reading["sentiment"] == "positive"
    # Fixed low, and below the threshold a real analyst pass requires.
    assert reading["confidence"] < 0.55


def test_lexicon_sentiment_reads_negative_terms():
    reading = lexicon_sentiment("The scandal and corruption charges sparked outrage")
    assert reading["sentiment"] == "negative"


def test_lexicon_sentiment_defaults_to_neutral_on_no_signal():
    reading = lexicon_sentiment("The meeting was held on Tuesday at the usual venue")
    assert reading["sentiment"] == "neutral"


def test_the_whole_scoring_stage_is_bounded_even_when_flaky(monkeypatch):
    """The production scenario: fails most calls, succeeds just often enough
    to never trip the circuit breaker (5-in-a-row), so nothing else would
    have stopped this from running unbounded."""
    from engine.config import settings

    monkeypatch.setattr(score_agent, "SCORE_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "agent_batch_size", 5, raising=False)
    calls = {"n": 0}

    def flaky(subject, batch, failures=None):
        calls["n"] += 1
        time.sleep(0.2)
        if failures is not None:
            failures.append("timeout")
        return {}

    monkeypatch.setattr(score_agent, "_score_batch", flaky)
    started = time.monotonic()
    result = score_agent.score_items("Subject", _items(60))
    elapsed = time.monotonic() - started

    assert elapsed < 4.0, f"scoring ran {elapsed:.1f}s past its 1s deadline"
    assert result == {}


def test_score_items_with_floor_is_bounded_and_still_fills_every_gap(monkeypatch):
    from engine.config import settings

    monkeypatch.setattr(score_agent, "SCORE_DEADLINE_SECONDS", 1.0)
    monkeypatch.setattr(settings, "agent_batch_size", 5, raising=False)

    def flaky(subject, batch, failures=None):
        time.sleep(0.2)
        return {}

    monkeypatch.setattr(score_agent, "_score_batch", flaky)
    started = time.monotonic()
    scored = score_agent.score_items_with_floor("Subject", _items(60))
    elapsed = time.monotonic() - started

    assert elapsed < 4.0
    assert len(scored) == 60
    assert all(v["source"] == "lexicon" for v in scored.values())


# --- integration: the floor must never reach the database -------------------

def test_the_report_time_floor_is_never_persisted_to_the_database(db_session, monkeypatch):
    """The one guarantee that matters most: a lexicon guess must never block a
    mention from being re-scored by a real model on a later, healthier run."""
    import hashlib
    from datetime import datetime, timedelta

    from engine.agents import score as score_module
    from engine.config import settings as config_settings
    from engine.db.models import MentionSentiment, RawMention
    from engine.pipeline import run_analysis
    from engine.tests.test_pipeline import make_politician

    monkeypatch.setattr(config_settings, "llm_provider", "stub")
    # Simulate total sentiment-scoring failure specifically, independent of
    # whatever the stub backend does for every other stage.
    monkeypatch.setattr(score_module, "score_items", lambda *a, **k: {})

    politician = make_politician(db_session)
    now = datetime.utcnow()
    for i in range(5):
        text = f"{politician.name} and the National Treasury discussed the loans {i}"
        db_session.add(RawMention(
            politician_id=politician.id, platform="news", source_type="article",
            author_handle=f"@voice{i}", text=text, posted_at=now - timedelta(days=i),
            raw_payload={"url": f"https://n/{i}"}, engagement_json={"likes": i},
            content_hash=hashlib.sha1(text.encode()).hexdigest(),
            source_url=f"https://n/{i}"))
    db_session.commit()

    report = run_analysis(db_session, politician, "weekly",
                          now - timedelta(days=30), now)

    # Nothing was written to the database — the whole point of keeping
    # score_items' original contract untouched.
    assert db_session.query(MentionSentiment).count() == 0

    # But the report the reader sees is not blank.
    breakdown = report.payload["sentiment_breakdown"]
    assert breakdown["total_mentions_analyzed"] > 0
    assert breakdown["positive_pct"] is not None or breakdown["negative_pct"] is not None \
        or breakdown["neutral_pct"] is not None

"""Nothing scored must not be reported as a reading of zero.

`total_sentiment = sum(counts.values()) or 1` was one variable doing two jobs:
a divisor that must never be zero, and the count reported to the reader. The
guard leaked into the payload, so a run that scored NOTHING reported
"1 analysed" and a headline sentiment score of 0.0%.

That is not a thin report. It presents a total scoring failure as a finding
about the subject, and it looks identical to a politician with genuinely no
positive coverage — which is the exact judgement a client would pay for.
"""

from datetime import datetime

from engine.reports import sentiment_framework as fw
from engine.reports.generator import generate_report_payload

WS, WE = datetime(2026, 6, 1), datetime(2026, 6, 30)


def _mention(i):
    return {
        "id": f"m{i}", "platform": "news", "source_type": "article",
        "author_handle": "nation.africa", "text": f"Kindiki story {i}",
        "posted_at": datetime(2026, 6, 10), "engagement": {}, "language": "en",
        "source_url": f"https://nation.africa/{i}",
    }


def test_nothing_scored_reports_nothing_scored(monkeypatch):
    monkeypatch.setattr("engine.llm.call_json", lambda *a, **k: {"summary": "s"})
    payload = generate_report_payload(
        "Githure Kindiki", WS, WE, [_mention(i) for i in range(36)], {}, [], [], {},
    )
    breakdown = payload["sentiment_breakdown"]
    assert breakdown["total_mentions_analyzed"] == 0, "the divide-by-zero guard leaked again"
    assert breakdown["positive_pct"] is None, "0.0% asserts there is no positive coverage"
    assert breakdown["negative_pct"] is None
    assert breakdown["coverage_pct"] == 0.0


def test_partial_scoring_reports_its_own_coverage(monkeypatch):
    monkeypatch.setattr("engine.llm.call_json", lambda *a, **k: {"summary": "s"})
    mentions = [_mention(i) for i in range(10)]
    sentiments = {f"m{i}": {"sentiment": "positive", "intensity": 3} for i in range(4)}
    payload = generate_report_payload("X", WS, WE, mentions, sentiments, [], [], {})
    breakdown = payload["sentiment_breakdown"]
    assert breakdown["total_mentions_analyzed"] == 4
    assert breakdown["positive_pct"] == 100.0  # of what was scored
    assert breakdown["coverage_pct"] == 40.0   # of what was collected


def test_the_headline_score_is_absent_not_zero():
    payload = {"volume_trends": {"total_mentions": 36},
               "sentiment_breakdown": {"total_mentions_analyzed": 0,
                                       "positive_pct": None, "negative_pct": None,
                                       "neutral_pct": None}}
    section = fw.build_sentiment_score_section(payload, None, {})
    assert section["score"] is None
    assert section["scoring_gap"] and "None of the 36" in section["scoring_gap"]


def test_a_minority_reading_says_so():
    payload = {"volume_trends": {"total_mentions": 420},
               "sentiment_breakdown": {"total_mentions_analyzed": 67}}
    sentiments = {f"m{i}": {"sentiment": "neutral"} for i in range(67)}
    section = fw.build_sentiment_score_section(payload, None, sentiments)
    assert "67 of 420" in section["scoring_gap"]


def test_a_fully_scored_corpus_carries_no_warning():
    payload = {"volume_trends": {"total_mentions": 10}}
    sentiments = {f"m{i}": {"sentiment": "positive"} for i in range(10)}
    section = fw.build_sentiment_score_section(payload, None, sentiments)
    assert section["scoring_gap"] is None
    assert section["score"] == 100.0


def test_the_ui_shows_absent_rather_than_a_zero_ring():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text(encoding="utf-8")
    assert "score.score==null" in html, "the ring still renders 0.0% for an unscored run"
    assert "not scored" in html
    assert "score.scoring_gap" in html


def test_a_delta_against_an_unscored_period_is_undefined():
    """"Unchanged at 0" would be a claim about the subject; the truth is that
    one of the two periods has no reading at all."""
    from engine.reports.deltas import compute_deltas

    class _Prev:
        payload = {"sentiment_breakdown": {"positive_pct": None, "neutral_pct": None,
                                           "negative_pct": None},
                   "volume_trends": {"total_mentions": 36}}
        generated_at = datetime(2026, 6, 1)

    class _Q:
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def first(self): return _Prev()

    class _DB:
        def query(self, *a, **k): return _Q()

    class _Pol:
        id = "p1"

    current = {"sentiment_breakdown": {"positive_pct": 40.0, "neutral_pct": 40.0,
                                       "negative_pct": 20.0},
               "volume_trends": {"total_mentions": 100}}
    deltas = compute_deltas(_DB(), _Pol(), current)
    assert deltas["sentiment_shift"]["positive"] is None
    assert deltas["mention_volume_change"] == 64

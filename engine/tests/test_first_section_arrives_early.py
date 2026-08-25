"""The first thing a reader gets should not wait on the model.

Volume, platform spread, recency and the loudest mentions are arithmetic over
rows already held. They were computed alongside sentiment and narratives, so
they arrived only AFTER entity linking, people extraction and whole-corpus
scoring — the longest stretch of a run. A reader waited ten minutes to be told
how many mentions there were.
"""

from datetime import datetime

from engine import api_server
from engine.reports.generator import corpus_preview

WINDOW_END = datetime(2026, 6, 30)


def _mention(i, platform="news", days_ago=1, likes=0, source_type="article"):
    from datetime import timedelta
    return {
        "id": f"m{i}",
        "platform": platform,
        "source_type": source_type,
        "author_handle": f"user{i}",
        "text": f"Mention {i}",
        "posted_at": WINDOW_END - timedelta(days=days_ago),
        "engagement": {"likes": likes},
        "language": "en",
        "source_url": f"https://example.com/{i}",
    }


def test_the_preview_needs_no_model_call(monkeypatch):
    """If it touched the LLM it would be back behind the same queue."""
    import engine.llm as llm

    def boom(*a, **k):
        raise AssertionError("the preview must not call the model")

    monkeypatch.setattr(llm, "call_json", boom)
    monkeypatch.setattr(llm, "call_json_untrusted", boom)

    preview = corpus_preview([_mention(0), _mention(1, platform="youtube")], WINDOW_END)
    assert preview["volume_trends"]["total_mentions"] == 2
    assert preview["volume_trends"]["by_platform"] == {"news": 1, "youtube": 1}


def test_unknown_sentiment_is_unknown_not_zero():
    """A 0 asserts that nothing is positive. Until scoring runs, the honest
    answer is that we do not know yet — and the UI renders null as "—"."""
    preview = corpus_preview([_mention(0)], WINDOW_END)
    sentiment = preview["sentiment_breakdown"]
    assert sentiment["positive_pct"] is None
    assert sentiment["negative_pct"] is None
    assert sentiment["total_mentions_analyzed"] == 0


def test_the_preview_carries_recency_and_the_loudest_mentions():
    mentions = [
        _mention(0, days_ago=2, likes=500),
        _mention(1, days_ago=20, likes=5),
        _mention(2, days_ago=200, likes=1),
    ]
    preview = corpus_preview(mentions, WINDOW_END)
    assert preview["volume_trends"]["last_7_days"] == 1
    assert preview["volume_trends"]["last_30_days"] == 2
    assert preview["notable_mentions"][0]["author_handle"] == "user0"


def test_an_undated_mention_does_not_break_the_preview():
    """Rows arrive from connectors that sometimes cannot date an item."""
    undated = _mention(0)
    undated["posted_at"] = None
    preview = corpus_preview([undated, _mention(1)], WINDOW_END)
    assert preview["volume_trends"]["total_mentions"] == 2
    assert preview["volume_trends"]["last_7_days"] == 1  # the undated one is not counted as recent




def test_the_preview_is_shapeable_into_a_frontend_payload(db_session, monkeypatch):
    """It has to survive _build_frontend_payload, or _publish_partial silently
    drops it and the reader still sees nothing."""
    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    from engine.db.models import Politician

    subject = Politician(name="Preview Subject")
    db_session.add(subject)
    db_session.commit()

    preview = corpus_preview([_mention(0), _mention(1, platform="youtube")], WINDOW_END)
    shaped = api_server._build_frontend_payload(
        subject, api_server._PartialReport(preview, datetime(2026, 1, 1), WINDOW_END)
    )
    assert shaped["volume"]["total"] == 2
    assert shaped["volume"]["platforms"] == 2
    # Null all the way through, so the UI shows "—" rather than claiming 0%.
    assert shaped["sentiment"]["positive"] is None
    assert shaped["narratives"] == []

from datetime import datetime

from engine.reports.generator import generate_report_payload


def make_mention(i, platform="tiktok", language="en", likes=0):
    return {
        "id": f"m{i}",
        "platform": platform,
        "source_type": "video",
        "author_handle": f"user{i}",
        "text": f"Mbadi mention number {i}",
        "posted_at": datetime(2026, 6, 10 + i),
        "engagement": {"likes": likes},
        "language": language,
        "source_url": f"https://example.com/{i}",
    }


def test_report_includes_language_source_and_notable_sections(monkeypatch):
    monkeypatch.setattr("engine.llm.call_json", lambda *a, **k: {"summary": "LLM summary of real stats."})
    mentions = [make_mention(0, likes=100), make_mention(1, platform="news", language="sw")]
    sentiments = {"m0": {"sentiment": "positive", "intensity": 4}, "m1": {"sentiment": "negative", "intensity": 2}}

    payload = generate_report_payload(
        "John Mbadi", datetime(2026, 6, 1), datetime(2026, 6, 30), mentions, sentiments, [], [],
        {"top_users": [], "top_people": [], "top_influencers": []},
    )

    assert payload["volume_trends"]["by_language"] == {"en": 1, "sw": 1}
    assert payload["volume_trends"]["by_source_type"] == {"video": 2}
    assert payload["executive_summary"] == "LLM summary of real stats."
    notable = payload["notable_mentions"]
    assert notable[0]["author_handle"] == "user0"  # highest engagement first
    assert notable[0]["source_url"] == "https://example.com/0"
    assert notable[0]["sentiment"] == "positive"


def test_report_summary_falls_back_when_llm_unavailable(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no api key")

    monkeypatch.setattr("engine.llm.call_json", boom)
    payload = generate_report_payload(
        "John Mbadi", datetime(2026, 6, 1), datetime(2026, 6, 30), [make_mention(0)],
        {"m0": {"sentiment": "neutral", "intensity": 3}}, [], [], {},
    )
    assert "John Mbadi had 1 mentions" in payload["executive_summary"]

"""A search that waits forty-five minutes and delivers one section.

Every analyst section falls back to `[]` or `{}` when its call fails, so a
provider that refuses everything produces a page with the locally-computed
sentiment on it and nothing else — after the full retry budget has been paid
on every one of ~20 calls. The mentions were collected, stored and counted the
whole time.

Two fixes, tested here:

  - a circuit breaker, so a provider that has failed repeatedly stops costing
    the reader four minutes per remaining call,
  - a deterministic floor, so the sections that do not need a model at all —
    which platforms carried this, who the loudest accounts were, when things
    happened — are produced by counting instead of left blank.
"""

import pytest

from engine import llm
from engine.reports import report_floor

MENTIONS = [
    {"id": f"m{i}", "platform": "x" if i % 2 else "news",
     "text": f"Uhuru Kenyatta and the National Treasury discussed the loans {i}",
     "author_handle": f"@voice{i % 3}", "posted_at": f"2026-06-0{i % 9 + 1}",
     "engagement": {"likes": i * 10}}
    for i in range(12)
]


# --- the breaker ------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_breaker():
    llm.reset_breaker()
    yield
    llm.reset_breaker()


def test_a_refusing_provider_stops_being_asked(monkeypatch):
    sent = {"n": 0}

    def boom(prompt, max_tokens=1024, model=None):
        sent["n"] += 1
        raise RuntimeError("429 refused")

    monkeypatch.setattr(llm, "_call_json", boom)
    blocked = 0
    for _ in range(30):
        try:
            llm.call_json("p")
        except llm.ProviderUnavailable:
            blocked += 1
        except RuntimeError:
            pass

    assert blocked > 15, "every call still paid the full retry budget"
    assert sent["n"] < 12, "the provider was still being asked on most calls"


def test_the_budget_shrinks_as_evidence_accumulates():
    """The first failure could be a bad minute. The fourth is not."""
    llm.reset_breaker()
    clean = llm._total_budget()
    llm._record_failure()
    after_one = llm._total_budget()
    llm._record_failure()
    llm._record_failure()
    after_three = llm._total_budget()

    assert after_one < clean
    assert after_three < after_one
    llm._record_success()
    assert llm._total_budget() == clean, "recovery must restore the full budget"


def test_one_call_in_every_few_still_probes_the_provider(monkeypatch):
    """A provider that comes back must be noticed."""
    calls = {"n": 0}

    def flaky(prompt, max_tokens=1024, model=None):
        calls["n"] += 1
        raise RuntimeError("429")

    monkeypatch.setattr(llm, "_call_json", flaky)
    for _ in range(40):
        try:
            llm.call_json("p")
        except Exception:  # noqa: BLE001
            pass
    assert calls["n"] > llm.BREAKER_THRESHOLD, "the breaker never probed again"


def test_a_success_closes_the_breaker(monkeypatch):
    for _ in range(llm.BREAKER_THRESHOLD):
        llm._record_failure()
    assert llm.breaker_state()["open"]
    llm._record_success()
    assert not llm.breaker_state()["open"]
    monkeypatch.setattr(llm, "_call_json", lambda *a, **k: {"ok": True})
    assert llm.call_json("p") == {"ok": True}


def test_starting_a_run_clears_it():
    for _ in range(llm.BREAKER_THRESHOLD):
        llm._record_failure()
    llm.reset_adaptive_gap()
    assert not llm.breaker_state()["open"]


# --- the floor --------------------------------------------------------------

def test_platform_pulse_needs_no_model():
    pulse = report_floor.platform_pulse(MENTIONS)
    assert [p["platform"] for p in pulse] == ["news", "x"]
    assert all(p["derived"] for p in pulse)
    assert pulse[0]["notable_voices"]
    assert "not a reading" in pulse[0]["tone"]


def test_the_loudest_accounts_need_no_model():
    voices = report_floor.influencer_stances(MENTIONS, [])
    assert voices
    assert all(v["stance"] == "not established" for v in voices)
    # Ranked by reach, so the most-engaged handle leads.
    assert voices[0]["handle"] == "@voice2"


def test_the_timeline_needs_no_model():
    events = report_floor.timeline(MENTIONS)
    assert events
    dates = [e["date"] for e in events]
    assert dates == sorted(dates)
    assert all(e["derived"] for e in events)


def test_the_summary_states_what_was_collected_and_claims_nothing():
    summary = report_floor.executive_summary(
        MENTIONS, "Uhuru Kenyatta", {"mentions_analyzed": 0})
    assert "12 items" in summary
    assert "did not answer" in summary
    # It must never say anything ABOUT the subject.
    assert "Uhuru Kenyatta were collected" in summary


def test_it_fills_only_what_is_missing():
    payload = {"platform_pulse": [{"platform": "x", "tone": "a real analyst wrote this"}],
               "influence_summary": [], "executive_summary": ""}
    filled = report_floor.fill(payload, MENTIONS, "Uhuru Kenyatta")

    assert "platform_pulse" not in filled
    assert payload["platform_pulse"][0]["tone"] == "a real analyst wrote this"
    assert {"timeline", "influencer_stances", "public_voice"} <= set(filled)
    assert payload["derived_sections"] == sorted(filled)


def test_no_mentions_means_nothing_is_invented():
    payload = {"executive_summary": ""}
    assert report_floor.fill(payload, [], "Nobody") == []
    assert "derived_sections" not in payload


def test_a_report_run_with_no_model_still_delivers_sections(monkeypatch):
    """End to end through the section writer, with every call refused."""
    from engine.reports import sections

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm, "_call_json", boom)
    monkeypatch.setattr(llm, "call_json_untrusted", boom)

    payload = {
        "executive_summary": "",
        "sentiment_breakdown": {"positive_pct": 30, "neutral_pct": 40, "negative_pct": 30,
                                "total_mentions_analyzed": len(MENTIONS)},
        "volume_trends": {"total_mentions": len(MENTIONS),
                          "by_platform": {"news": 6, "x": 6}, "by_day": {}},
        "influence_summary": [], "narrative_breakdown": [],
    }
    published: list[str] = []
    from datetime import datetime, timedelta

    end = datetime(2026, 6, 10)
    out = sections.enrich_report_payload(
        "Uhuru Kenyatta", end - timedelta(days=30), end, payload, mentions=MENTIONS,
        on_section=lambda key, value: published.append(key))

    assert out["platform_pulse"], "the page would show sentiment and nothing else"
    assert out["timeline"]
    assert out["derived_sections"]
    # And the reader is told, as the sections stream.
    assert "platform_pulse" in published
    assert "derived_sections" in published

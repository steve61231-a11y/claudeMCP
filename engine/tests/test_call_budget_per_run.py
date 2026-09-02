"""Every avoidable model call is a section lost on a throttled key.

A run that worked well was followed by runs that failed on the same free
model. Part of that was mine: splitting public voice into three calls and the
timeline into two added three requests per run, at the front of the analyst
stage, on a key that refuses after six.

Splitting is right on a big corpus, where one reply would be truncated. It is
waste on an ordinary one. These tests pin that the cost is paid only when the
corpus needs it.
"""

from datetime import datetime

import pytest

from engine import health, llm
from engine.reports import analysts


@pytest.fixture(autouse=True)
def _fresh():
    health.reset()
    health.reset_probe_cache()
    yield
    health.reset()
    health.reset_probe_cache()


def _mentions(n):
    return [{"id": f"m{i:06d}", "text": f"A distinct thing said number {i}.",
             "platform": "x", "source_type": "post", "author_handle": f"a{i}",
             "posted_at": datetime(2026, 8, 1 + (i % 27)), "engagement": {"views": i}}
            for i in range(n)]


# --- public voice ------------------------------------------------------------

def test_an_ordinary_corpus_costs_one_call_not_three(monkeypatch):
    calls = []

    def once(instructions, untrusted, expected_keys, max_tokens,
             max_untrusted_chars, model=None):
        calls.append(instructions)
        return {"supportive": [{"theme": "t", "summary": "s", "quotes": []}],
                "critical": [], "neutral": []}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", once)
    voice = analysts.analyze_public_voice("X", _mentions(120))
    assert len(calls) == 1, f"{len(calls)} calls for a 120-mention corpus"
    assert voice["supportive"]


def test_a_large_corpus_still_splits_by_stance(monkeypatch):
    """One reply for a corpus this size is 3,000+ words and gets truncated."""
    calls = []

    def per_stance(instructions, untrusted, expected_keys, max_tokens,
                   max_untrusted_chars, model=None):
        calls.append(instructions)
        return {"themes": [{"theme": "t", "summary": "s", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", per_stance)
    analysts.analyze_public_voice("X", _mentions(analysts.SPLIT_PUBLIC_VOICE_ABOVE + 50))
    assert len(calls) == 3


def test_a_failed_single_call_falls_back_to_splitting(monkeypatch):
    """Saving a request must never cost the section."""
    calls = []

    def flaky(instructions, untrusted, expected_keys, max_tokens,
              max_untrusted_chars, model=None):
        calls.append(instructions)
        if expected_keys == {"supportive"}:
            raise RuntimeError("cut off")
        return {"themes": [{"theme": "t", "summary": "s", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", flaky)
    voice = analysts.analyze_public_voice("X", _mentions(50))
    assert len(calls) == 4, "one attempt, then three stances"
    assert voice["supportive"]


def test_an_empty_single_call_reply_falls_back_too(monkeypatch):
    calls = []

    def empty_then_full(instructions, untrusted, expected_keys, max_tokens,
                        max_untrusted_chars, model=None):
        calls.append(expected_keys)
        if expected_keys == {"supportive"}:
            return {"supportive": [], "critical": [], "neutral": []}
        return {"themes": [{"theme": "t", "summary": "s", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", empty_then_full)
    voice = analysts.analyze_public_voice("X", _mentions(50))
    assert len(calls) == 4
    assert voice["critical"]


# --- timeline ----------------------------------------------------------------

def test_an_ordinary_timeline_costs_one_call(monkeypatch):
    calls = []

    def once(instructions, untrusted, expected_keys, max_tokens,
             max_untrusted_chars, model=None):
        calls.append(instructions)
        return {"timeline": [{"date": "2026-08-01", "event": "e", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", once)
    mentions = _mentions(40)
    by_day = {m["posted_at"].date().isoformat(): 1 for m in mentions}
    analysts.analyze_timeline("X", mentions, by_day)
    assert len(calls) == 1


def test_a_long_timeline_is_still_split(monkeypatch):
    calls = []

    def halves(instructions, untrusted, expected_keys, max_tokens,
               max_untrusted_chars, model=None):
        calls.append(instructions)
        return {"timeline": [{"date": "2026-08-01", "event": "e", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", halves)
    # analyze_timeline first narrows to the ten busiest days, so the split is
    # decided on THAT sample, not the whole corpus. Concentrate the mentions so
    # the sample is genuinely large.
    from datetime import datetime as _dt

    mentions = _mentions(analysts.SPLIT_TIMELINE_ABOVE + 40)
    for i, mention in enumerate(mentions):
        mention["posted_at"] = _dt(2026, 8, 1 + (i % 5))
    by_day = {m["posted_at"].date().isoformat(): 20 for m in mentions}
    analysts.analyze_timeline("X", mentions, by_day)
    assert len(calls) == 2


# --- the liveness probe ------------------------------------------------------

def test_the_probe_is_not_repaid_on_every_run(monkeypatch):
    """The backend does not become misconfigured between two runs a minute
    apart, and a request spent re-confirming that is one not spent on the
    report — which matters precisely when requests are scarce."""
    calls = []
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "m")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: calls.append(1) or {"ok": True})

    for _ in range(5):
        health.preflight()
    assert len(calls) == 1


def test_a_cached_probe_says_it_is_cached(monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "m")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: {"ok": True})
    health.preflight()
    assert health.preflight().get("cached") is True


def test_a_failed_probe_is_never_cached(monkeypatch):
    """Caching a failure would keep a run degraded after the cause was fixed."""
    calls = []
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "m")
    monkeypatch.setattr(llm, "call_json",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(
                            RuntimeError("HTTP 429 rate limited")))
    health.preflight()
    health.preflight()
    assert len(calls) == 2, "a degraded probe was cached and never retried"

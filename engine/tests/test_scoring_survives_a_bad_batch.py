"""One bad batch must not cost the whole corpus.

Scoring ran 25 items per call and a failed call returned {}. With a corpus of
109 that is five calls, so a single systematic failure — one oversized reply,
one truncation, one item the model chokes on — scored NOTHING, and the report
presented that as a 0.0% sentiment reading.

Splitting turns a total loss into a partial one, and the stage now reports what
it managed and why anything was lost.
"""

import pytest

from engine.agents import score as score_agent
from engine.config import settings


def _items(n, prefix="m"):
    return [(f"{prefix}{i}", f"text number {i}") for i in range(n)]


def _reply(items):
    return {"scores": [
        {"i": pos, "sentiment": "neutral", "intensity": 3,
         "stance": "neutral_report", "topic": "t", "language": "en"}
        for pos in range(1, len(items) + 1)
    ]}


def test_a_batch_that_fails_whole_is_split_and_mostly_recovered(monkeypatch):
    """The production shape: big batches fail, small ones work."""
    monkeypatch.setattr(settings, "agent_batch_size", 25)
    monkeypatch.setattr(settings, "llm_max_concurrency", 1, raising=False)

    def flaky(instructions, untrusted, expected_keys, max_tokens,
              max_untrusted_chars, model=None):
        count = instructions.count("\n[") + instructions.count("[1]")
        n = len([ln for ln in instructions.splitlines() if ln.startswith("[")])
        if n > 6:
            raise RuntimeError("openai_compatible call failed: reply cut off")
        return _reply(range(n))

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", flaky)

    report: dict = {}
    scored = score_agent.score_items("Kindiki", _items(50), report=report)

    assert report["eligible"] == 50
    assert report["scored"] > 25, f"splitting recovered only {report['scored']} of 50"
    assert report["failures"], "the reason the big batches failed was not recorded"


def test_a_total_failure_says_why(monkeypatch):
    monkeypatch.setattr(settings, "agent_batch_size", 25)

    def boom(*a, **k):
        raise RuntimeError("ProviderRejectedRequest: HTTP 400 reasoning is mandatory")

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", boom)
    report: dict = {}
    assert score_agent.score_items("Kindiki", _items(30), report=report) == {}
    assert report["scored"] == 0
    assert report["eligible"] == 30
    assert any("reasoning is mandatory" in f for f in report["failures"])


def test_identical_failures_are_reported_once(monkeypatch):
    """Twenty batches failing the same way is one fact, not twenty."""
    monkeypatch.setattr(settings, "agent_batch_size", 5)

    def boom(*a, **k):
        raise RuntimeError("the same error every time")

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", boom)
    report: dict = {}
    score_agent.score_items("X", _items(60), report=report)
    assert len(report["failures"]) == 1


def test_a_healthy_run_reports_full_coverage_and_no_failures(monkeypatch):
    monkeypatch.setattr(settings, "agent_batch_size", 25)

    def ok(instructions, untrusted, expected_keys, max_tokens,
           max_untrusted_chars, model=None):
        n = len([ln for ln in instructions.splitlines() if ln.startswith("[")])
        return _reply(range(n))

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", ok)
    report: dict = {}
    scored = score_agent.score_items("X", _items(40), report=report)
    assert len(scored) == 40
    assert report["scored"] == 40
    assert report["failures"] == []


def test_splitting_stops_rather_than_recursing_forever(monkeypatch):
    """A permanently failing item must not spawn unbounded retries."""
    monkeypatch.setattr(settings, "agent_batch_size", 25)
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("always fails")

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", boom)
    score_agent.score_items("X", _items(25), report={})
    assert calls["n"] < 40, f"runaway splitting: {calls['n']} calls for 25 items"


def test_the_batch_budget_respects_the_configured_ceiling(monkeypatch):
    """min(8000, ...) hard-coded one provider's limit into the highest-volume
    stage, silently overriding LLM_MAX_OUTPUT_TOKENS."""
    monkeypatch.setattr(settings, "agent_batch_size", 25)
    monkeypatch.setattr(score_agent.llm, "max_output_tokens", lambda: 32000)
    seen: dict = {}

    def capture(instructions, untrusted, expected_keys, max_tokens,
                max_untrusted_chars, model=None):
        seen["max_tokens"] = max_tokens
        n = len([ln for ln in instructions.splitlines() if ln.startswith("[")])
        return _reply(range(n))

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", capture)
    score_agent.score_items("X", _items(25), report={})
    assert seen["max_tokens"] == 120 * 25 + 400

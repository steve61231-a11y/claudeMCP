"""Batched scoring — the whole corpus gets analysed, not a top-N slice.

An unread mention is indistinguishable from evidence that was never collected,
so these tests pin the property that matters: no silent cap. They also pin the
safety rules — a failed batch must not write wrong values, and items must not
get their neighbour's score when the model returns a partial answer.
"""

from engine.agents import score as score_agent
from engine.config import settings


def _items(n, prefix="item"):
    return [(f"id-{i}", f"{prefix} number {i}") for i in range(n)]


def test_every_item_is_scored_no_cap(monkeypatch):
    """1,000 items in, 1,000 scored out — the old path capped at 300."""
    monkeypatch.setattr(settings, "agent_batch_size", 25, raising=False)
    seen: list[int] = []

    def fake_batch(subject, batch):
        seen.append(len(batch))
        return {
            item_id: {"sentiment": "neutral", "intensity": 3, "context_tag": None,
                      "topic": "t", "language": "en", "confidence": 0.8, "source": "llm_batch"}
            for item_id, _ in batch
        }

    monkeypatch.setattr(score_agent, "_score_batch", fake_batch)
    scored = score_agent.score_items("Subject", _items(1000))

    assert len(scored) == 1000
    # ~40 calls instead of 1,000 — the reason the cap is no longer needed.
    assert len(seen) == 40
    assert max(seen) <= 25


def test_batch_failure_leaves_items_unscored_for_retry(monkeypatch):
    """A failed batch must not invent values; the items stay unscored so a
    later run retries them."""
    monkeypatch.setattr(settings, "agent_batch_size", 10, raising=False)

    calls = {"n": 0}

    def flaky(subject, batch):
        calls["n"] += 1
        if calls["n"] == 1:
            return {}  # this batch failed
        return {i: {"sentiment": "negative", "intensity": 4, "context_tag": None,
                    "topic": None, "language": None, "confidence": 0.8,
                    "source": "llm_batch"} for i, _ in batch}

    monkeypatch.setattr(score_agent, "_score_batch", flaky)
    scored = score_agent.score_items("Subject", _items(20))

    assert len(scored) == 10, "only the successful batch contributes"
    assert all(v["sentiment"] == "negative" for v in scored.values())


def test_scores_map_back_to_the_right_items(monkeypatch):
    """Positional refs must not drift: item 3's verdict belongs to item 3."""
    monkeypatch.setattr(settings, "agent_batch_size", 50, raising=False)

    captured = {}

    def fake_call(instructions, untrusted, expected_keys, max_tokens, max_untrusted_chars, model):
        captured["prompt"] = instructions
        # Deliberately out of order, and skipping one item.
        return {"scores": [
            {"i": 3, "sentiment": "negative", "intensity": 5, "stance": "attack", "topic": "graft", "language": "en"},
            {"i": 1, "sentiment": "positive", "intensity": 2, "stance": "praise", "topic": "budget", "language": "en"},
        ]}

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", fake_call)
    monkeypatch.setattr(score_agent.llm, "bulk_model", lambda: "bulk")

    scored = score_agent.score_items("Subject", [("a", "first"), ("b", "second"), ("c", "third")])

    assert scored["a"]["sentiment"] == "positive"
    assert scored["c"]["sentiment"] == "negative"
    assert "b" not in scored, "unanswered items stay unscored rather than guessed"


def test_invalid_model_output_is_sanitised(monkeypatch):
    def fake_call(instructions, untrusted, expected_keys, max_tokens, max_untrusted_chars, model):
        return {"scores": [
            {"i": 1, "sentiment": "furious", "intensity": 99, "stance": "attack"},
        ]}

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", fake_call)
    monkeypatch.setattr(score_agent.llm, "bulk_model", lambda: "bulk")

    scored = score_agent.score_items("Subject", [("a", "text")])

    assert scored["a"]["sentiment"] == "neutral"      # unknown label falls back
    assert scored["a"]["intensity"] == 5              # clamped into range


def test_uses_the_cheap_bulk_model(monkeypatch):
    """Bulk stages must not run on the expensive model — that economics is what
    makes full-corpus analysis viable."""
    used = {}

    def fake_call(instructions, untrusted, expected_keys, max_tokens, max_untrusted_chars, model):
        used["model"] = model
        return {"scores": []}

    monkeypatch.setattr(score_agent.llm, "call_json_untrusted", fake_call)
    monkeypatch.setattr(score_agent.llm, "bulk_model", lambda: "cheap-model")
    score_agent.score_items("Subject", [("a", "text")])

    assert used["model"] == "cheap-model"


def test_no_items_is_a_no_op(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("must not call the model with nothing to score")

    monkeypatch.setattr(score_agent, "_score_batch", fail)
    assert score_agent.score_items("Subject", []) == {}

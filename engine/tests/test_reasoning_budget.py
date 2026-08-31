"""A reasoning model that spends its whole budget thinking.

From a live run, five identical failures:

    RuntimeError: openai_compatible call failed after 4 attempts:
    empty reply from model (finish_reason=length,
    usage={'prompt_tokens': 1355, 'completion_tokens': 1480, ...})

The completion counts were 1480, 3400, 640, 880 and 1000 — and every one is
exactly the budget we asked for: 120*9+400, 120*25+400, 120*2+400, 120*4+400,
120*5+400. Our own formula caused them. It sized the budget for the JSON
answer, with no headroom for a model that reasons first, and reasoning is
charged against the same budget.

Worse, the growth ladder that exists for exactly this could never fire:
`_reply_text` raised ValueError on the empty reply BEFORE the finish_reason
was inspected, so the request was retried four times at the identical budget,
failing deterministically each time.
"""

import pytest

from engine import llm


def _response(finish_reason, content=None, reasoning=None, completion_tokens=1480):
    message = {}
    if content is not None:
        message["content"] = content
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1355, "completion_tokens": completion_tokens}}


# --- the budget helper -------------------------------------------------------

def test_a_tiny_output_budget_is_raised_to_the_reasoning_floor():
    assert llm.budget_for(640) == llm.REASONING_FLOOR
    assert llm.budget_for(1480) == llm.REASONING_FLOOR


def test_the_floor_is_big_enough_for_the_budgets_that_failed(monkeypatch):
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 32000)
    for failed in (640, 880, 1000, 1480, 3400):
        assert llm.budget_for(failed) > failed, f"{failed} would fail again"


def test_the_provider_ceiling_is_still_respected(monkeypatch):
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    assert llm.budget_for(999_999) == 8000


def test_a_large_expected_output_is_not_shrunk(monkeypatch):
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 32000)
    assert llm.budget_for(12_000) == 12_000


# --- the empty-and-truncated reply --------------------------------------------

def test_an_empty_truncated_reply_raises_truncation_not_a_value_error(monkeypatch):
    """This is the whole defect: ValueError went to the retry loop, which
    resent the identical request; TruncatedReply goes to the growth ladder."""
    captured = {}

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return _response("length", content="", reasoning="")

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(llm, "_throttle", lambda: None)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://provider.test/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "k")

    with pytest.raises(llm.TruncatedReply) as caught:
        llm._openai_compatible_json("prompt", 640, "some/reasoning-model")
    captured["cut"] = caught.value
    assert captured["cut"].produced_nothing is True


def test_a_truncated_reply_that_did_produce_text_is_not_flagged_as_empty(monkeypatch):
    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return _response("length", content='{"scores": [{"i": 1,')

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(llm, "_throttle", lambda: None)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://provider.test/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "k")

    with pytest.raises(llm.TruncatedReply) as caught:
        llm._openai_compatible_json("prompt", 640, "m")
    assert caught.value.produced_nothing is False
    assert '"scores"' in caught.value.partial_text


def test_an_empty_reply_that_was_not_truncated_is_still_an_error(monkeypatch):
    """A model returning nothing for a reason OTHER than length is a genuine
    failure and must not be retried with a bigger budget forever."""
    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return _response("stop", content="")

    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(llm, "_throttle", lambda: None)
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://provider.test/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "k")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError) as caught:
        llm._openai_compatible_json("prompt", 640, "m")
    assert "empty reply" in str(caught.value)


# --- the ladder grows fast enough to matter ----------------------------------

def test_a_reply_that_produced_nothing_jumps_rather_than_doubling(monkeypatch):
    """Doubling from 640 takes five paid round trips to reach a workable size,
    each one failing. The first retry must land somewhere usable."""
    budgets = []

    def fake(prompt, max_tokens=1024, model=None):
        budgets.append(max_tokens)
        if len(budgets) == 1:
            cut = llm.TruncatedReply("cut")
            cut.partial_text = ""
            cut.produced_nothing = True
            raise cut
        return {"ok": True}

    monkeypatch.setattr(llm, "_openai_compatible_json",
                        lambda prompt, mt, model: fake(prompt, mt, model))
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 32000)
    monkeypatch.setattr(llm, "strong_model", lambda: "m")
    monkeypatch.setattr(llm, "_cache_read", lambda k: None)
    monkeypatch.setattr(llm, "_cache_write", lambda k, v: None)

    llm._call_json("prompt", max_tokens=640)
    assert len(budgets) == 2
    assert budgets[1] >= llm.REASONING_FLOOR, (
        f"second attempt asked for {budgets[1]}, still too small to think in")


def test_the_ladder_stops_at_the_provider_ceiling(monkeypatch):
    budgets = []

    def always_cut(prompt, mt, model):
        budgets.append(mt)
        cut = llm.TruncatedReply("cut")
        cut.partial_text = ""
        cut.produced_nothing = True
        raise cut

    monkeypatch.setattr(llm, "_openai_compatible_json", always_cut)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    monkeypatch.setattr(llm, "strong_model", lambda: "m")
    monkeypatch.setattr(llm, "_cache_read", lambda k: None)
    monkeypatch.setattr(llm, "_cache_write", lambda k, v: None)
    monkeypatch.setattr(llm, "salvage_truncated_json", lambda t: None)

    with pytest.raises(llm.TruncatedReply):
        llm._call_json("prompt", max_tokens=640)
    assert max(budgets) <= 8000, "the ladder must not exceed the provider ceiling"


# --- the stages that failed no longer ask for too little ---------------------

@pytest.mark.parametrize("items", [2, 4, 5, 9, 25])
def test_scoring_now_asks_for_enough_to_think_in(items, monkeypatch):
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 32000)
    assert llm.budget_for(120 * items + 400) >= llm.REASONING_FLOOR


def test_every_batched_stage_routes_through_the_budget_helper():
    """Each of these computed an output-only budget and produced one of the
    live failures. A new stage doing the same must be caught here."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for relative in ("agents/score.py", "agents/verify.py", "agents/resolve.py",
                     "agents/knowledge_graph.py", "processing/entities.py",
                     "intelligence/narratives.py", "evidence/records.py"):
        source = (root / relative).read_text()
        assert "budget_for(" in source, (
            f"{relative} sizes a completion budget without reasoning headroom")

"""Spend the operator can see.

Usage was recorded on the Anthropic path only, and priced at Anthropic's
rates whatever served the call. Someone running on a $4 OpenRouter balance
therefore saw an empty bill for an account they could watch draining, and
had no way to answer "can I afford another run" except by running one.
"""

from engine import llm
from engine.config import settings


def test_openai_style_usage_is_recorded(monkeypatch):
    """OpenAI-protocol providers report prompt_tokens / completion_tokens, not
    input_tokens / output_tokens. Nothing read the former, so every paid
    OpenRouter call recorded zero."""
    seen = {}
    monkeypatch.setattr(llm, "_persist_usage", lambda i, o: seen.update(inp=i, out=o))
    llm._record_openai_usage({"usage": {"prompt_tokens": 1200, "completion_tokens": 340}})
    assert seen == {"inp": 1200, "out": 340}


def test_a_reply_with_no_usage_block_is_not_an_error(monkeypatch):
    monkeypatch.setattr(llm, "_persist_usage", lambda i, o: None)
    llm._record_openai_usage({})
    llm._record_openai_usage({"usage": None})


def test_accounting_never_breaks_a_call(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("database is down")
    monkeypatch.setattr(llm, "_persist_usage", boom)
    llm._record_openai_usage({"usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def test_zero_tokens_are_not_written(monkeypatch):
    """An empty rollup row is noise in the only record of what was spent."""
    calls = []
    monkeypatch.setattr(llm, "_persist_usage", lambda i, o: calls.append((i, o)))
    llm._record_openai_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0}})
    assert calls == [(0, 0)], "the guard belongs in _persist_usage, not the caller"


def test_the_openai_backend_has_its_own_prices():
    """Pricing an OpenRouter run at Anthropic's rates overstated it several
    times over."""
    assert settings.llm_price_in != settings.anthropic_price_in
    assert settings.llm_price_out != settings.anthropic_price_out

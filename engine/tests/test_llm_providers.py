"""Stand-in LLM backends, and the label that keeps them out of client work.

Testing on a cheap model is fine. Shipping a report produced on one, unlabelled,
is not — the prompts, JSON contracts and the whole anti-hallucination layer are
tuned against the production model. So the provider switch and the provenance
stamp are tested together: the escape hatch and the guard rail are one feature.
"""

import json

import pytest

from engine import llm


@pytest.fixture(autouse=True)
def anthropic_default(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(llm.settings, "anthropic_model", "claude-strong")
    monkeypatch.setattr(llm.settings, "anthropic_bulk_model", "claude-bulk")
    monkeypatch.delenv("LLM_CACHE_DIR", raising=False)


# --- provenance -------------------------------------------------------------

def test_anthropic_runs_are_marked_production():
    grade = llm.report_grade()
    assert grade["production"] is True
    assert grade["model"] == "claude-strong" and grade["bulk_model"] == "claude-bulk"
    assert "warning" not in grade


@pytest.mark.parametrize("backend", ["stub", "openai_compatible"])
def test_stand_in_runs_are_marked_not_for_clients(backend, monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", backend)

    grade = llm.report_grade()

    assert grade["production"] is False
    assert grade["backend"] == backend
    assert "must not be shown to a client" in grade["warning"]
    assert llm.is_test_grade() is True


# --- model selection per provider -------------------------------------------

def test_provider_switch_selects_that_providers_model_names(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_model", "deepseek-chat")
    monkeypatch.setattr(llm.settings, "llm_bulk_model", "deepseek-chat-lite")

    assert llm.strong_model() == "deepseek-chat"
    assert llm.bulk_model() == "deepseek-chat-lite"


def test_bulk_falls_back_to_the_strong_model_when_unset(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4")
    monkeypatch.setattr(llm.settings, "llm_bulk_model", "")

    assert llm.bulk_model() == "glm-4"


# --- the OpenAI-compatible path ---------------------------------------------

def _fake_post(captured, content):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    def post(url, headers=None, json=None, timeout=None):
        captured.update({"url": url, "headers": headers, "body": json})
        return _Resp()

    return post


def _use_openai_compatible(monkeypatch, content):
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(llm.settings, "llm_model", "deepseek-chat")
    captured: dict = {}
    monkeypatch.setattr(requests, "post", _fake_post(captured, content))
    return captured


def test_openai_compatible_request_shape(monkeypatch):
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))

    result = llm.call_json("a prompt", max_tokens=500)

    assert result == {"ok": True}
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["body"]["model"] == "deepseek-chat"
    assert captured["body"]["messages"] == [{"role": "user", "content": "a prompt"}]


def test_fenced_and_chatty_replies_still_parse(monkeypatch):
    """Stand-in providers wrap JSON in code fences and add preamble; Claude
    doesn't. Failing to handle it would make every cheap run look broken."""
    _use_openai_compatible(monkeypatch, 'Sure!\n```json\n{"ok": true}\n```')
    assert llm.call_json("p", max_tokens=100) == {"ok": True}

    _use_openai_compatible(monkeypatch, '```\n{"ok": true}\n```')
    assert llm.call_json("p", max_tokens=100) == {"ok": True}


def test_a_reply_with_no_json_is_an_error_not_a_silent_empty(monkeypatch):
    _use_openai_compatible(monkeypatch, "I cannot help with that request.")
    with pytest.raises(ValueError, match="no JSON found"):
        llm.call_json("p", max_tokens=100)


def test_missing_base_url_fails_loudly(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "")
    with pytest.raises(RuntimeError, match="llm_base_url"):
        llm.call_json("p", max_tokens=100)


# --- the stub backend -------------------------------------------------------

def test_stub_makes_no_network_call_and_returns_usable_shapes(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider", "stub")

    def _explode(*a, **k):
        raise AssertionError("the stub backend must never touch the network")

    monkeypatch.setattr(llm, "get_client", _explode)
    import requests
    monkeypatch.setattr(requests, "post", _explode)

    digest = llm.call_json('give me a "digest" of this batch', max_tokens=100)
    assert "digest" in digest and digest["digest"]["claims"] == []

    # An unrecognised prompt returns an empty object — the same thing every
    # caller already handles when a real call fails.
    assert llm.call_json("something unrecognised", max_tokens=100) == {}

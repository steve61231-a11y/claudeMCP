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
        status_code = 200

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
    """A model that answers in prose is retried — one rambling reply is often
    followed by a compliant one. If it never complies the call raises, naming
    the cause: a section that silently returns nothing reads as "no findings",
    which in a due-diligence report is the most dangerous failure there is."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    _use_openai_compatible(monkeypatch, "I cannot help with that request.")

    with pytest.raises(RuntimeError, match="no JSON found"):
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


# --- DeepSeek-specific behaviour --------------------------------------------

def test_output_budget_is_capped_to_the_provider_ceiling(monkeypatch):
    """DeepSeek hard-400s above 8192 output tokens. The truncation retry doubles
    max_tokens, so an uncapped request would fail the whole section."""
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))

    llm.call_json("give me json", max_tokens=100000)

    assert captured["body"]["max_tokens"] == llm.OPENAI_COMPATIBLE_MAX_TOKENS


def test_json_mode_requested_only_when_the_prompt_says_json(monkeypatch):
    """DeepSeek rejects response_format=json_object unless the prompt contains
    the word 'json'. Asking for it unconditionally would fail every call."""
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    llm.call_json("Respond with ONLY this JSON: {...}", max_tokens=100)
    assert captured["body"]["response_format"] == {"type": "json_object"}

    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    llm.call_json("Summarise the following.", max_tokens=100)
    assert "response_format" not in captured["body"]


def _flaky_post(monkeypatch, statuses, content):
    """Answers with the given status codes in order, then succeeds."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "deepseek-chat")
    monkeypatch.setattr(llm, "OPENAI_COMPATIBLE_RETRIES", 4)
    calls = {"n": 0}

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = "throttled"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    def post(url, headers=None, json=None, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        return _Resp(statuses[i] if i < len(statuses) else 200)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(llm.time if hasattr(llm, "time") else __import__("time"), "sleep", lambda s: None)
    return calls


def test_throttling_is_retried_not_dropped(monkeypatch):
    """Free tiers throttle hard and the map step fires several requests at once.
    A 429 must not cost us a chunk of the corpus."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    calls = _flaky_post(monkeypatch, [429, 429], json.dumps({"ok": True}))

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert calls["n"] == 3, "did not retry through the throttling"


def test_server_errors_are_retried(monkeypatch):
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    calls = _flaky_post(monkeypatch, [503], json.dumps({"ok": True}))

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert calls["n"] == 2


def test_persistent_failure_raises_with_the_cause(monkeypatch):
    """A section that silently returns nothing looks like 'no findings', which
    for a due-diligence report is the most dangerous possible failure."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    _flaky_post(monkeypatch, [429, 429, 429, 429], json.dumps({"ok": True}))

    with pytest.raises(RuntimeError, match="failed after"):
        llm.call_json("give me json", max_tokens=100)


# --- free-tier rate limits --------------------------------------------------

def test_no_concurrency_ceiling_by_default(monkeypatch):
    """The paid path should keep its parallelism."""
    monkeypatch.setattr(llm.settings, "llm_max_concurrency", 0)
    assert llm.concurrency(6) == 6


def test_concurrency_is_clamped_for_free_tiers(monkeypatch):
    """Free tiers cap requests-per-minute well below what the map step wants.
    Firing six at once turns a throttled run into what looks like a broken one."""
    monkeypatch.setattr(llm.settings, "llm_max_concurrency", 2)
    assert llm.concurrency(6) == 2
    assert llm.concurrency(1) == 1, "never raises a caller's own lower limit"


def test_concurrency_never_drops_below_one(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_max_concurrency", 1)
    assert llm.concurrency(6) == 1


def test_every_parallel_stage_respects_the_ceiling(monkeypatch):
    """A ceiling honoured by the map step but ignored by scoring or verification
    still produces the 429 storm it exists to prevent."""
    import inspect

    from engine.agents import disambiguate, resolve, score, verify
    from engine.reports import digest

    for module in (digest, disambiguate, score, verify, resolve):
        source = inspect.getsource(module)
        pools = source.count("ThreadPoolExecutor(")
        guarded = source.count("llm.concurrency(")
        assert guarded >= pools, (
            f"{module.__name__}: {pools} thread pool(s) but only {guarded} "
            "guarded by llm.concurrency()"
        )

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
    for a due-diligence report is the most dangerous possible failure.

    Uses 500s, not 429s: a rate limit means the request was never served, so it
    has its own budget and its own test (see the rate-limiting section)."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    _flaky_post(monkeypatch, [503, 503, 503, 503], json.dumps({"ok": True}))

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


def test_json_mode_is_dropped_and_retried_when_a_model_rejects_it(monkeypatch):
    """Not every free model implements JSON mode, and those that don't reject
    the whole request. One 400 must not fail the run — _extract_json already
    copes with the fenced, chatty replies that come back without it."""
    import time as _time

    import requests

    monkeypatch.setattr(_time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "some/free-model:free")

    seen = []

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = "response_format is not supported"

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(str(self.status_code))

        def json(self):
            return {"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]}

    def post(url, headers=None, json=None, timeout=None):
        seen.append("response_format" in json)
        return _Resp(400 if seen[-1] else 200)

    monkeypatch.setattr(requests, "post", post)

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert seen == [True, False], "did not retry without JSON mode"


def test_health_reports_the_live_backend_and_precache_state(monkeypatch):
    """Two things that are otherwise invisible from outside: which model is
    actually serving, and whether the pre-cache is short-circuiting the pipeline
    so a provider switch looks like it did nothing."""
    from fastapi.testclient import TestClient

    from engine import api_server

    monkeypatch.setattr(api_server.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(api_server.settings, "llm_model", "glm-4.5-flash")
    monkeypatch.setattr(api_server.settings, "serve_precache_first", True)

    body = TestClient(api_server.app).get("/api/health").json()

    assert body["llm"]["backend"] == "openai_compatible"
    assert body["llm"]["model"] == "glm-4.5-flash"
    assert body["llm"]["production"] is False
    assert body["serving_precache"] is True


# --- reasoning models (GLM and friends) -------------------------------------

def test_thinking_is_disabled_for_glm(monkeypatch):
    """GLM-4.5/4.6 are hybrid reasoning models: with thinking on they spend the
    budget reasoning and return an empty `content`, which is exactly the
    'no JSON found in ...' failure this prevents."""
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4.5-flash")

    llm.call_json("give me json", max_tokens=100)

    assert captured["body"]["thinking"] == {"type": "disabled"}


def test_thinking_is_not_sent_to_other_providers(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    llm.call_json("give me json", max_tokens=100)
    assert "thinking" not in captured["body"]


def test_extra_body_lets_a_provider_quirk_be_fixed_by_config(monkeypatch):
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_extra_body", '{"top_k": 5}')

    llm.call_json("give me json", max_tokens=100)

    assert captured["body"]["top_k"] == 5


def test_malformed_extra_body_is_ignored_not_fatal(monkeypatch):
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_extra_body", "not json at all")

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert "top_k" not in captured["body"]


def test_answer_is_read_from_reasoning_content_when_content_is_empty(monkeypatch):
    """Reasoning models split their output. When the budget runs out mid-thought
    `content` is '' and the only thing present is the reasoning — using it beats
    reporting an empty reply."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.z.ai/api/paas/v4")
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4.5-flash")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": "",
                "reasoning_content": 'I should answer: {"ok": true}',
            }}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}


def test_a_wholly_empty_reply_explains_why(monkeypatch):
    """'no JSON found in ...' says nothing about the cause. The finish reason and
    usage are what tell you the budget went on thinking."""
    import time as _time

    import requests

    monkeypatch.setattr(_time, "sleep", lambda s: None)
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.z.ai/api/paas/v4")
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4.5-flash")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"completion_tokens": 100}}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(RuntimeError) as excinfo:
        llm.call_json("give me json", max_tokens=100)

    message = str(excinfo.value)
    assert "finish_reason=length" in message
    assert "thinking" in message.lower()


def test_reasoning_toggle_matches_the_provider_not_the_model_name(monkeypatch):
    """Every gateway spells the thinking toggle differently, and an unrecognised
    field is rejected outright rather than ignored. Keying off the endpoint
    means a brand-new reasoning model works without a code change; a name-prefix
    check would silently stop matching."""
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")

    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "dots-studio/dots-3-note-preview:free")
    llm.call_json("give me json", max_tokens=100)
    assert captured["body"]["reasoning"] == {"enabled": False}
    assert "thinking" not in captured["body"], "sent Z.ai's spelling to OpenRouter"

    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.z.ai/api/paas/v4")
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4.5-flash")
    llm.call_json("give me json", max_tokens=100)
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "reasoning" not in captured["body"], "sent OpenRouter's spelling to Z.ai"


def test_unknown_provider_gets_no_reasoning_field(monkeypatch):
    """Guessing would risk a 400 on every call; the empty-reply fallback covers
    us instead."""
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://some-new-gateway.example/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "brand-new-model")

    llm.call_json("give me json", max_tokens=100)

    assert "reasoning" not in captured["body"] and "thinking" not in captured["body"]


def test_chunk_size_is_configurable_to_fit_a_daily_request_cap(monkeypatch):
    """Free tiers meter requests per day, not tokens. On a large-context model
    bigger chunks mean fewer calls, which is the difference between finishing a
    report and hitting the cap."""
    from engine.reports import digest as digest_module

    mentions = [{"id": f"m{i}", "platform": "x", "source_type": "post",
                 "text": "text about the subject " * 40, "author_handle": "a",
                 "posted_at": None, "engagement": {}} for i in range(80)]

    monkeypatch.setattr(digest_module.llm.settings, "llm_chunk_chars", 0)
    default_chunks = len(digest_module._chunk_mentions(mentions))

    monkeypatch.setattr(digest_module.llm.settings, "llm_chunk_chars", 160000)
    big_chunks = len(digest_module._chunk_mentions(mentions))

    assert big_chunks < default_chunks, "raising the budget did not reduce calls"
    # Coverage is the guarantee that must survive any chunk size.
    assert sum(len(c) for c in digest_module._chunk_mentions(mentions)) == 80


# --- rate limiting ----------------------------------------------------------

def _rate_limited(monkeypatch, statuses, content, retry_after=None):
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://api.z.ai/api/paas/v4")
    monkeypatch.setattr(llm.settings, "llm_model", "glm-4.5-flash")
    monkeypatch.setattr(llm.settings, "llm_extra_body", "")
    monkeypatch.setattr(llm.settings, "llm_min_request_interval_ms", 0)
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", slept.append)

    calls = {"n": 0}

    class _Resp:
        def __init__(self, status):
            self.status_code = status
            self.text = '{"error":{"code":"1302","message":"Rate limit reached"}}'
            self.headers = {"Retry-After": str(retry_after)} if retry_after else {}

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
    return calls, slept


def test_a_rate_limit_waits_out_the_window_not_a_few_seconds(monkeypatch):
    """Z.ai code 1302 is a per-MINUTE quota. Backing off ~7 seconds across four
    attempts spends them all inside the same blocked window, so the call fails
    having never actually retried."""
    calls, slept = _rate_limited(monkeypatch, [429, 429], json.dumps({"ok": True}))

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert calls["n"] == 3
    assert max(slept) >= 15, f"backoff too short to outlast a quota window: {slept}"


def test_rate_limits_do_not_consume_the_attempt_budget(monkeypatch):
    """A throttled request was never served, so it says nothing about whether
    the call would succeed — counting it as a failed attempt burns the budget
    on the provider's clock rather than on real errors."""
    calls, _ = _rate_limited(monkeypatch, [429] * 5, json.dumps({"ok": True}))

    assert llm.call_json("give me json", max_tokens=100) == {"ok": True}
    assert calls["n"] == 6, "gave up while only ever being rate limited"


def test_the_providers_own_retry_after_is_obeyed(monkeypatch):
    calls, slept = _rate_limited(monkeypatch, [429], json.dumps({"ok": True}), retry_after=42)

    llm.call_json("give me json", max_tokens=100)

    assert 42 in slept, f"ignored Retry-After: {slept}"


def test_persistent_rate_limiting_still_gives_up(monkeypatch):
    """Waiting forever would hang the report instead of failing it."""
    _rate_limited(monkeypatch, [429] * 50, json.dumps({"ok": True}))

    with pytest.raises(RuntimeError, match="rate limited"):
        llm.call_json("give me json", max_tokens=100)


def test_requests_can_be_spaced_to_stay_under_a_quota(monkeypatch):
    """Capping concurrency is not enough: released workers still fire at once
    and a per-minute quota sees a burst."""
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", slept.append)
    monkeypatch.setattr(llm.settings, "llm_min_request_interval_ms", 500)
    llm._LAST_REQUEST_AT = _time.monotonic()

    llm._throttle()

    assert slept and slept[0] > 0, "did not space the request"


def test_spacing_is_off_by_default(monkeypatch):
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", slept.append)
    monkeypatch.setattr(llm.settings, "llm_min_request_interval_ms", 0)
    # Spacing also grows on its own when the provider throttles us, and that
    # learned value is process-wide and deliberately does not decay. This test
    # is about the CONFIGURED default, so start from none learned.
    llm.reset_adaptive_gap()

    llm._throttle()

    assert slept == []


def test_spacing_learned_from_throttling_applies_even_when_none_is_configured(monkeypatch):
    """Being refused is evidence about the limit. Ignoring it because nobody
    set LLM_MIN_REQUEST_INTERVAL_MS walks straight back into the limit."""
    import time as _time

    slept: list[float] = []
    monkeypatch.setattr(_time, "sleep", slept.append)
    monkeypatch.setattr(llm.settings, "llm_min_request_interval_ms", 0)
    llm.reset_adaptive_gap()
    llm._widen_spacing()
    try:
        llm._throttle()
        llm._throttle()
        assert any(gap > 0 for gap in slept), "a throttled key was hit at full speed again"
    finally:
        llm.reset_adaptive_gap()


# --- a rejected request must say WHY ----------------------------------------

def _rejecting_post(monkeypatch, status, body, count=None):
    """A provider that rejects every request with `status` and `body`."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(llm.settings, "llm_model", "stealth/ox-alpha")

    class _Resp:
        status_code = status
        text = body
        headers: dict = {}

        def raise_for_status(self):
            raise requests.HTTPError(f"{status} Client Error: Bad Request for url: x")

        def json(self):
            return json.loads(body)

    def post(url, headers=None, json=None, timeout=None):
        if count is not None:
            count["n"] = count.get("n", 0) + 1
        return _Resp()

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)


def test_a_rejected_request_carries_the_providers_own_explanation(monkeypatch):
    """raise_for_status() produces "400 Client Error: Bad Request for url: ..."
    and discards the body — the one place the provider says what is wrong. A
    bad model id looked identical to a bad key, an unsupported parameter and a
    malformed prompt."""
    _rejecting_post(
        monkeypatch, 400,
        '{"error":{"message":"stealth/ox-alpha is not a valid model ID","code":400}}',
    )
    with pytest.raises(llm.ProviderRejectedRequest) as excinfo:
        llm.call_json("respond with json", max_tokens=100)

    message = str(excinfo.value)
    assert "not a valid model ID" in message
    assert "HTTP 400" in message
    assert "stealth/ox-alpha" in message  # names the model it was sent


def test_a_bad_key_is_reported_as_a_bad_key(monkeypatch):
    _rejecting_post(monkeypatch, 401, '{"error":{"message":"No auth credentials found"}}')
    with pytest.raises(llm.ProviderRejectedRequest) as excinfo:
        llm.call_json("respond with json", max_tokens=100)
    assert "No auth credentials found" in str(excinfo.value)


def test_a_rejected_request_is_not_retried(monkeypatch):
    """A 4xx never succeeds on retry; sending it again spends a timeout to
    arrive at the same answer. Only the JSON-mode 400 is worth a second go."""
    count: dict = {}
    _rejecting_post(monkeypatch, 401, '{"error":{"message":"nope"}}', count=count)
    with pytest.raises(llm.ProviderRejectedRequest):
        llm.call_json("respond with json", max_tokens=100)
    assert count["n"] == 1


def test_json_mode_is_still_dropped_and_retried_on_a_400(monkeypatch):
    """The one 400 that IS worth retrying: a model that doesn't implement JSON
    mode rejects the whole request, and the reply parses fine without it."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "some/model")
    seen: list[dict] = []

    class _Resp:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text
            self.headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'},
                                 "finish_reason": "stop"}]}

    def post(url, headers=None, json=None, timeout=None):
        seen.append(dict(json))
        if "response_format" in json:
            return _Resp(400, '{"error":{"message":"response_format unsupported"}}')
        return _Resp(200)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)

    assert llm.call_json("respond with json", max_tokens=100) == {"ok": True}
    assert "response_format" in seen[0]
    assert "response_format" not in seen[-1]


def _adaptive_post(monkeypatch, reject_when, model="stealth/ox-alpha"):
    """A provider that 400s while `reject_when(body)` holds, then succeeds."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(llm.settings, "llm_model", model)
    seen: list[dict] = []

    class _Resp:
        def __init__(self, status, text=""):
            self.status_code = status
            self.text = text
            self.headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'},
                                 "finish_reason": "stop"}]}

    def post(url, headers=None, json=None, timeout=None):
        seen.append(dict(json))
        rejection = reject_when(json)
        if rejection:
            return _Resp(400, rejection)
        return _Resp(200)

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)
    return seen


def test_a_model_that_mandates_reasoning_is_accommodated(monkeypatch):
    """We disable reasoning by default so hybrid models don't spend the whole
    budget thinking and return an empty answer. Some models refuse to have it
    switched off, and a blanket rule keyed off the endpoint cannot know which."""
    seen = _adaptive_post(
        monkeypatch,
        lambda body: ('{"error":{"message":"Reasoning is mandatory for this '
                      'endpoint and cannot be disabled.","code":400}}'
                      if "reasoning" in body else None),
    )
    assert llm.call_json("respond with json", max_tokens=100) == {"ok": True}
    assert "reasoning" in seen[0], "the first attempt should still try to disable it"
    assert "reasoning" not in seen[-1], "the retry must drop it"


def test_the_field_named_in_the_error_is_the_one_dropped(monkeypatch):
    """Dropping JSON mode when the provider complained about reasoning would
    degrade parsing for no reason."""
    seen = _adaptive_post(
        monkeypatch,
        lambda body: ('{"error":{"message":"Reasoning cannot be disabled"}}'
                      if "reasoning" in body else None),
    )
    llm.call_json("respond with json", max_tokens=100)
    assert "response_format" in seen[-1], "JSON mode was dropped needlessly"


def test_adapting_does_not_spend_the_retry_budget(monkeypatch):
    """Each drop removes that field for good, so adapting is bounded — it must
    not eat the attempts reserved for genuinely transient failures."""
    monkeypatch.setattr(llm, "OPENAI_COMPATIBLE_RETRIES", 1)
    seen = _adaptive_post(
        monkeypatch,
        lambda body: ('{"error":{"message":"Reasoning is mandatory"}}'
                      if "reasoning" in body else None),
    )
    assert llm.call_json("respond with json", max_tokens=100) == {"ok": True}
    assert len(seen) == 2


def test_a_400_naming_nothing_we_sent_still_fails_loudly(monkeypatch):
    """Once our own fields are gone, a 400 is about the request itself and must
    reach the operator rather than looping."""
    seen = _adaptive_post(monkeypatch, lambda body: '{"error":{"message":"context too long"}}')
    with pytest.raises(llm.ProviderRejectedRequest) as excinfo:
        llm.call_json("respond with json", max_tokens=100)
    assert "context too long" in str(excinfo.value)


# --- a cut-off reply is retried, not reported as unparseable -----------------

def _truncating_post(monkeypatch, budget_that_succeeds: int):
    """A provider that truncates until max_tokens reaches the given budget."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_api_key", "sk-test")
    monkeypatch.setattr(llm.settings, "llm_model", "stealth/ox-alpha")
    budgets: list[int] = []

    class _Resp:
        def __init__(self, body):
            self.status_code = 200
            self.text = ""
            self.headers = {}
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    def post(url, headers=None, json=None, timeout=None):
        budgets.append(json["max_tokens"])
        if json["max_tokens"] < budget_that_succeeds:
            # Real shape of the failure: valid JSON, cut off mid-string.
            return _Resp({"choices": [{"message": {"content": '{"label": "Gachagua\'s DCP tours", "description": "Live-streamed cov'},
                                       "finish_reason": "length"}]})
        return _Resp({"choices": [{"message": {"content": '{"label": "DCP tours", "description": "done"}'},
                                   "finish_reason": "stop"}]})

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)
    return budgets


def test_a_cut_off_reply_is_retried_with_a_bigger_budget(monkeypatch):
    """A model that MANDATES reasoning charges thinking against the same
    budget, so a limit that used to be ample now runs out mid-sentence. The
    Anthropic path has always retried this; this one reported "no JSON found"
    with a fragment of perfectly good JSON attached."""
    budgets = _truncating_post(monkeypatch, budget_that_succeeds=800)
    assert llm.call_json("respond with json", max_tokens=200) == {"label": "DCP tours",
                                                                  "description": "done"}
    # The ladder now JUMPS rather than doubling: max_tokens is a cap, not a
    # charge, so climbing in small steps only buys extra failed round trips.
    assert budgets[0] == 200
    assert budgets[-1] > budgets[0], f"the budget never grew: {budgets}"
    assert len(budgets) <= 3, f"too many paid round trips to reach a usable budget: {budgets}"


def test_a_half_written_field_is_never_presented_as_complete(monkeypatch):
    """Salvage keeps what closed cleanly and drops the rest. The cut-off field
    must not appear at all — a truncated value served as a whole one is worse
    than a missing section, because nothing downstream can tell."""
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 200, raising=False)
    _truncating_post(monkeypatch, budget_that_succeeds=10_000)
    result = llm.call_json("respond with json", max_tokens=200)
    assert result == {"label": "Gachagua's DCP tours"}
    assert "description" not in result


def test_nothing_recoverable_still_fails_loudly(monkeypatch):
    """Salvage is not a licence to invent an answer."""
    import requests

    monkeypatch.setattr(llm.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(llm.settings, "llm_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(llm.settings, "llm_model", "m")
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 200, raising=False)

    class _Resp:
        status_code = 200
        text = ""
        headers: dict = {}

        def raise_for_status(self):
            pass

        def json(self):
            # Cut before a single member completes: nothing to keep.
            return {"choices": [{"message": {"content": '{"key_actors":[{"name":"Ru'},
                                 "finish_reason": "length"}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(__import__("time"), "sleep", lambda s: None)
    with pytest.raises(llm.TruncatedReply):
        llm.call_json("respond with json", max_tokens=200)


def test_the_ladder_stops_at_the_backend_ceiling(monkeypatch):
    """It must climb to the ceiling and no further — asking for more than the
    provider allows is a hard 400 on some backends."""
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 800, raising=False)
    budgets = _truncating_post(monkeypatch, budget_that_succeeds=10_000)
    llm.call_json("respond with json", max_tokens=200)
    assert max(budgets) == 800, f"ran past the ceiling: {budgets}"


# --- the configured ceiling must reach the wire ------------------------------

def test_the_configured_ceiling_is_what_is_actually_sent(monkeypatch):
    """LLM_MAX_OUTPUT_TOKENS was accepted, reported by max_output_tokens(), and
    then silently discarded: the request body clamped to DeepSeek's 8000 no
    matter what an operator set. Raising it did nothing, and every section that
    needed more truncated anyway."""
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 32000, raising=False)

    llm.call_json("a prompt", max_tokens=20000)
    assert captured["body"]["max_tokens"] == 20000, (
        f"sent {captured['body']['max_tokens']}, not the requested budget"
    )


def test_the_provider_default_still_caps_an_unconfigured_run(monkeypatch):
    captured = _use_openai_compatible(monkeypatch, json.dumps({"ok": True}))
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 0, raising=False)
    llm.call_json("a prompt", max_tokens=99999)
    assert captured["body"]["max_tokens"] == llm.OPENAI_COMPATIBLE_MAX_TOKENS


# --- salvaging a cut-off array ----------------------------------------------

def test_a_flat_object_cut_mid_value_keeps_its_finished_fields():
    """The real failure shape: three long prose fields, the third cut off."""
    text = ('{"involvement":"long text here","tension_or_risk":"more text",'
            '"verdict":"cut off ri')
    out = llm.salvage_truncated_json(text)
    assert out["involvement"] == "long text here"
    assert out["tension_or_risk"] == "more text"
    assert "verdict" not in out, "a half-written field must not be presented as complete"


def test_complete_elements_survive_a_cut_off_array():
    text = ('{"key_actors":[{"name":"Ruto","relation":"architect"},'
            '{"name":"Treasury","relation":"funds it"},{"name":"Bou')
    out = llm.salvage_truncated_json(text)
    assert [a["name"] for a in out["key_actors"]] == ["Ruto", "Treasury"]


def test_a_string_containing_brackets_does_not_confuse_the_salvage():
    text = '{"items":[{"note":"see [1] and {2}"},{"note":"second"},{"note":"thi'
    out = llm.salvage_truncated_json(text)
    assert len(out["items"]) == 2
    assert out["items"][0]["note"] == "see [1] and {2}"


def test_an_escaped_quote_does_not_confuse_the_salvage():
    text = '{"items":[{"note":"he said \\"yes\\""},{"note":"second"},{"no'
    out = llm.salvage_truncated_json(text)
    assert len(out["items"]) == 2


def test_nothing_complete_salvages_to_nothing():
    """Better to fail honestly than to present an empty object as an answer."""
    assert llm.salvage_truncated_json('{"key_actors":[{"name":"Ru') is None
    assert llm.salvage_truncated_json("") is None
    assert llm.salvage_truncated_json("not json at all") is None


def test_salvage_is_the_last_resort_not_the_first(monkeypatch):
    """It must only run once the budget cannot grow — otherwise a section that
    would have come back whole is quietly served short."""
    monkeypatch.setattr(llm.settings, "llm_max_output_tokens", 800, raising=False)
    budgets = _truncating_post(monkeypatch, budget_that_succeeds=10_000)
    result = llm.call_json("respond with json", max_tokens=200)
    # It climbed to the ceiling first...
    assert budgets[0] == 200 and budgets[-1] == 800
    # ...then salvaged what was complete rather than losing the section.
    assert result == {"label": "Gachagua's DCP tours"}
    assert "description" not in result

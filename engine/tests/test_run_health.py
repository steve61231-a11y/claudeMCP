"""A broken backend must never be presentable as a thin subject.

Reproduces the live failure: LLM_MODEL was set to `stealth/ox-alpha`, an
OpenRouter cloaked preview that had been retired. Every call returned HTTP 404
with "Thank you for participating". Narrative labelling fell back to keywords,
scoring returned 0 of 109, every analyst section came back empty — and the page
rendered a complete-looking report. Around 120 individually-correct `except`
handlers added up to a system that could not report its own total failure.
"""

import pytest

from engine import health

RETIRED = ("ProviderRejectedRequest: openai_compatible rejected the request: HTTP 404 "
           "from https://openrouter.ai/api/v1/chat/completions (model='stealth/ox-alpha') "
           '— {"error":{"message":"Thank you for participating"}}')


@pytest.fixture(autouse=True)
def _fresh():
    health.reset()
    yield
    health.reset()


# --- accounting --------------------------------------------------------------

def test_a_clean_run_is_ok_and_says_nothing():
    tracker = health.current()
    for _ in range(20):
        tracker.record_success()
    assert tracker.verdict == health.VERDICT_OK
    assert tracker.usable
    assert tracker.headline() is None, "a healthy run must not nag"


def test_a_few_failures_are_degraded_not_broken():
    tracker = health.current()
    for _ in range(19):
        tracker.record_success()
    tracker.record_failure(RuntimeError("one oversized batch"))
    assert tracker.verdict == health.VERDICT_DEGRADED
    assert tracker.usable, "one bad batch is normal degradation, not a dead run"
    assert "incomplete" in tracker.headline()


def test_the_live_failure_is_reported_as_not_an_analysis():
    tracker = health.current()
    for _ in range(60):
        tracker.record_failure(RuntimeError(RETIRED))
    assert tracker.verdict == health.VERDICT_BROKEN
    assert tracker.usable is False
    headline = tracker.headline()
    assert "not an analysis" in headline
    assert "not because there was nothing to find" in headline
    assert "stealth/ox-alpha" in headline, "name the model that failed"


def test_a_run_that_made_no_calls_is_not_reported_as_broken():
    assert health.current().verdict == health.VERDICT_OK


def test_accounting_is_threadsafe():
    from concurrent.futures import ThreadPoolExecutor

    tracker = health.current()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: tracker.record_success() if i % 2
                      else tracker.record_failure(RuntimeError("x")), range(400)))
    assert tracker.calls == 400
    assert tracker.failures == 200


# --- the llm seam ------------------------------------------------------------

def test_every_call_is_counted_at_the_one_seam(monkeypatch):
    from engine import llm

    monkeypatch.setattr(llm, "_call_json", lambda *a, **k: {"ok": True})
    llm.call_json("x")
    llm.call_json("y")
    assert health.current().calls == 2
    assert health.current().failures == 0


def test_a_raising_call_is_counted_as_a_failure_and_still_raises(monkeypatch):
    from engine import llm

    def boom(*a, **k):
        raise RuntimeError(RETIRED)

    monkeypatch.setattr(llm, "_call_json", boom)
    with pytest.raises(RuntimeError):
        llm.call_json("x")
    assert health.current().failures == 1


# --- preflight ---------------------------------------------------------------

def test_preflight_turns_a_retired_model_into_an_instruction(monkeypatch):
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "stealth/ox-alpha")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(RETIRED)))

    with pytest.raises(health.PreflightFailed) as caught:
        health.preflight()
    remedy = caught.value.remedy
    assert "does not exist on this provider" in remedy
    assert "LLM_MODEL" in remedy, "say the exact variable to change"
    assert "stealth" in remedy


@pytest.mark.parametrize("error,expected", [
    ("HTTP 401 unauthorized", "LLM_API_KEY"),
    ("HTTP 402 insufficient credit", "Top up"),
    ("HTTP 429 rate limit exceeded", "rate-limiting"),
    ("ConnectTimeout: could not connect", "LLM_BASE_URL"),
])
def test_each_provider_failure_has_its_own_remedy(error, expected):
    assert expected in health._remedy_for(error, "m", "openai_compatible")


def test_preflight_passes_when_the_model_answers(monkeypatch):
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "good/model")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: {"ok": True, "n": 2})
    assert health.preflight()["ok"] is True


def test_preflight_rejects_a_model_that_cannot_return_json(monkeypatch):
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "prose/only")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: "I am happy to help!")
    with pytest.raises(health.PreflightFailed):
        health.preflight()


def test_the_stub_backend_needs_no_call(monkeypatch):
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "stub")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: pytest.fail("no call for stub"))
    assert health.preflight()["ok"] is True


def test_summary_is_json_safe_for_the_payload():
    import json

    tracker = health.current()
    tracker.record_failure(RuntimeError(RETIRED))
    json.dumps(tracker.summary())

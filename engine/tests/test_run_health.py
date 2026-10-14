"""A broken backend must never be presentable as a thin subject.

Reproduces the live failure: LLM_MODEL was set to `stealth/ox-alpha`, an
OpenRouter cloaked preview that had been retired. Every call returned HTTP 404
with "Thank you for participating". Narrative labelling fell back to keywords,
scoring returned 0 of 109, every analyst section came back empty — and the page
rendered a complete-looking report. Around 120 individually-correct `except`
handlers added up to a system that could not report its own total failure.
"""

import os
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


# ---------------------------------------------------------------------------
# The remedy that sent an operator to the wrong product.
#
# A live run died with "Missing Authentication header" and the page answered:
# "The API key was rejected. Set a valid LLM_API_KEY in the Render dashboard."
# Two things wrong in one sentence, on the service's only error screen.
#
# The app had been on Railway for weeks, so the operator was sent to a
# dashboard that is not theirs — the same failure as naming a menu that
# cannot exist, which this repository has already lost an evening to. And
# the key had not been rejected: the provider never received one. "Wrong
# value" and "variable not set" are different problems with different fixes,
# and both arrive as HTTP 401.
# ---------------------------------------------------------------------------


def test_the_remedy_names_the_host_the_operator_is_actually_on(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    remedy = health._remedy_for("HTTP 401 unauthorized", "m", "openai_compatible")
    assert "Railway" in remedy
    assert "Render" not in remedy, (
        "the operator is being sent to a dashboard they do not use")


def test_no_remedy_hardcodes_a_host(monkeypatch):
    """Whatever the environment says, nothing may assume a host. This is the
    property that failed, not any one string."""
    for name in list(os.environ):
        if name.startswith(("RAILWAY_", "RENDER", "FLY_")):
            monkeypatch.delenv(name, raising=False)
    for error in ("HTTP 404 not found", "HTTP 401 unauthorized",
                  "HTTP 500 something else entirely"):
        remedy = health._remedy_for(error, "m", "openai_compatible")
        assert "Render" not in remedy and "Railway" not in remedy, (
            f"a host is hardcoded into the remedy for {error!r}: {remedy}")


def test_a_key_that_was_never_sent_is_not_a_key_that_was_rejected():
    """The live error. OpenRouter said "Missing Authentication header" — it
    received no key at all. Telling the operator their key was rejected
    sends them to check a value that does not exist."""
    missing = health._remedy_for(
        'HTTP 401 from openrouter — {"error":{"message":"Missing Authentication '
        'header","code":401}}', "deepseek/deepseek-v4-flash-0731", "openai_compatible")
    assert "not set" in missing, "an unset variable is reported as a bad value"
    assert "rejected" not in missing.split("which is different")[0], (
        "the remedy still calls an absent key a rejected one")

    rejected = health._remedy_for("HTTP 401 unauthorized: invalid api key",
                                  "m", "openai_compatible")
    assert "rejected" in rejected or "refused" in rejected
    assert rejected != missing, (
        "both 401s produce the same advice, so the one distinction that "
        "changes what the operator does has been lost")


def test_the_remedy_names_the_variable_this_backend_actually_reads():
    """LLM_API_KEY is never read on the Anthropic path. Naming it there
    sends someone to set a variable with no effect, and the next run fails
    identically — which reads as the advice being wrong."""
    assert "ANTHROPIC_API_KEY" in health._remedy_for(
        "HTTP 401 unauthorized", "m", "anthropic")
    assert "LLM_API_KEY" in health._remedy_for(
        "HTTP 401 unauthorized", "m", "openai_compatible")

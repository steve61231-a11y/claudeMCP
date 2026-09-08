"""The run that failed every section against a provider that was working.

From a live paid-model run, every stage reported as failed: digest chunks 1-9,
event resolution, the executive brief, the executive summary, grounding
verification, influencer stances and every narrative deep-dive. "The model did
not answer." The model was answering.

The mechanism was a feedback loop between three things that were each
defensible alone:

  - a reasoning model spends thinking tokens out of the SAME allowance as the
    answer, so an under-sized budget returns an empty `content` and raises
    `TruncatedReply`;
  - `_record_failure` counted that toward `_CONSECUTIVE_FAILURES`, which is
    meant to measure "is the PROVIDER refusing us";
  - `_total_budget()` then shrank on that count — to 45s after three — which
    is less than one thinking call needs, so the next call timed out, which
    counted again, and at five the breaker opened and refused every remaining
    section without sending anything.

One under-budgeted call thereby took down the whole run, and the report blamed
the provider for it.

The fix is to separate "the provider would not serve us" from "we asked for
the wrong thing". Only the first is evidence the backend is down.
"""

import json

import pytest

from engine import llm


@pytest.fixture(autouse=True)
def _clean_breaker():
    llm.reset_breaker()
    yield
    llm.reset_breaker()


# --- what counts as the provider's fault -------------------------------------

@pytest.mark.parametrize("exc", [
    llm.TruncatedReply("cut off"),
    ValueError("no JSON found in ''"),
    json.JSONDecodeError("bad", "{", 0),
])
def test_a_reply_we_could_not_use_is_not_evidence_the_provider_is_down(exc):
    """These all mean a response ARRIVED. Whatever was wrong with it, the
    provider served the request — which is the only thing the breaker is
    entitled to have an opinion about."""
    for _ in range(10):
        llm._record_failure(exc)
    assert llm.breaker_state()["consecutive_failures"] == 0
    assert not llm.breaker_state()["open"]


@pytest.mark.parametrize("exc", [
    ConnectionError("connection reset"),
    TimeoutError("gave up after 240s"),
    RuntimeError("HTTP 503"),
])
def test_a_provider_that_will_not_serve_us_still_trips_the_breaker(exc):
    for _ in range(llm.BREAKER_THRESHOLD):
        llm._record_failure(exc)
    assert llm.breaker_state()["open"], "the breaker must still stop a dead backend"


def test_an_unclassified_failure_is_still_treated_as_the_providers_fault():
    """Fail closed: an exception this module has not seen before should keep
    the old protective behaviour rather than silently disabling the breaker."""
    for _ in range(llm.BREAKER_THRESHOLD):
        llm._record_failure(RuntimeError("something new"))
    assert llm.breaker_state()["open"]


# --- the budget must not collapse below what one call needs -------------------

def test_the_budget_never_shrinks_below_a_single_thinking_call():
    """45s was less than one reasoning call on a full digest chunk, so the
    shrink stopped being a budget and became a guarantee of failure."""
    for failures in range(0, llm.BREAKER_THRESHOLD + 1):
        llm.reset_breaker()
        for _ in range(failures):
            llm._record_failure(ConnectionError("down"))
        assert llm._total_budget() >= llm.OPENAI_COMPATIBLE_TIMEOUT, \
            f"after {failures} failures the budget is under one attempt's timeout"


def test_truncated_replies_alone_never_shrink_the_budget():
    full = llm._total_budget()
    for _ in range(10):
        llm._record_failure(llm.TruncatedReply("cut off"))
    assert llm._total_budget() == full


# --- the two constants the loop ran on ---------------------------------------

def test_one_call_cannot_consume_the_whole_analyst_deadline():
    """The analyst deadline is wall-clock over the whole fan-out. A per-call
    budget equal to it means the first call spends it and every other analyst
    is abandoned unstarted."""
    from engine.config import settings

    assert llm.OPENAI_COMPATIBLE_TOTAL_BUDGET < settings.analyst_deadline_seconds


def test_the_total_budget_leaves_room_for_more_than_one_attempt():
    assert llm.OPENAI_COMPATIBLE_TOTAL_BUDGET >= 2 * llm.OPENAI_COMPATIBLE_TIMEOUT


# --- our ceiling must not become someone else's hard failure ------------------

def test_a_provider_with_a_lower_output_cap_is_clamped_to_not_rejected():
    """Our ceiling is 32000 because a reasoning model needs the room. DeepSeek
    caps at 8192. Hard-coding either number is wrong for the other, so take the
    limit the provider names rather than failing the call."""
    body = {"max_tokens": 32000}
    assert llm._clamp_max_tokens(
        body, "invalid max_tokens: must be <= 8192") == 8192
    assert body["max_tokens"] == 8192


def test_a_budget_complaint_with_no_number_is_halved_rather_than_failed():
    body = {"max_tokens": 32000}
    assert llm._clamp_max_tokens(body, "max_tokens too large") == 16000


def test_a_400_about_something_else_is_left_alone():
    body = {"max_tokens": 32000}
    assert llm._clamp_max_tokens(body, "unknown model 'gpt-9'") is None
    assert body["max_tokens"] == 32000


def test_an_already_small_budget_is_not_blamed_for_the_rejection():
    body = {"max_tokens": 512}
    assert llm._clamp_max_tokens(body, "max_tokens invalid") is None

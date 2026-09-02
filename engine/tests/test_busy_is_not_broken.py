"""A busy provider must not stop a run; a misconfigured one must.

Live report: "The analysis model is not answering, so this run was stopped
before it collected anything… rate-limited 6 time(s)."

Stopping the run was my call and it was wrong. A rate limit is not
misconfiguration — the same key that is throttled this second commonly serves
the next call, and the stage ledger already names whatever fails. A thin
report beats no report. Only failures a SETTING can fix should stop a run:
a retired model, a refused key, an exhausted balance.

The probe also spent 300 seconds reaching that verdict, which is four minutes
of a run's life spent learning something it could have discovered while
working.
"""

import pytest

from engine import health, llm


@pytest.fixture(autouse=True)
def _fresh():
    health.reset()
    llm.reset_adaptive_gap()
    yield
    health.reset()
    llm.reset_adaptive_gap()


def _preflight_raising(monkeypatch, error: Exception):
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "some/model")
    monkeypatch.setattr(llm, "call_json",
                        lambda *a, **k: (_ for _ in ()).throw(error))


# --- busy: carry on ----------------------------------------------------------

RATE_LIMITED = RuntimeError(
    "openai_compatible call failed after 6 request(s): gave up after 300s: "
    "6 request(s) sent, rate-limited 6 time(s).")


def test_a_rate_limited_provider_does_not_stop_the_run(monkeypatch):
    _preflight_raising(monkeypatch, RATE_LIMITED)
    result = health.preflight()
    assert result["ok"] is True
    assert result["degraded"] is True
    assert "continued" in result["note"]


def test_the_throttled_probe_is_still_counted_as_a_failure(monkeypatch):
    """Carrying on is not the same as pretending it worked."""
    _preflight_raising(monkeypatch, RATE_LIMITED)
    health.preflight()
    assert health.current().failures == 1


@pytest.mark.parametrize("error", [
    RuntimeError("HTTP 429 too many requests"),
    TimeoutError("read timed out"),
    ConnectionError("connection reset by peer"),
])
def test_every_transient_failure_lets_the_run_proceed(monkeypatch, error):
    _preflight_raising(monkeypatch, error)
    assert health.preflight().get("degraded") is True


# --- misconfigured: stop -----------------------------------------------------

@pytest.mark.parametrize("error,expected_in_remedy", [
    (RuntimeError("HTTP 404 (model='stealth/ox-alpha') Thank you for participating"), "LLM_MODEL"),
    (RuntimeError("HTTP 401 unauthorized"), "LLM_API_KEY"),
    (RuntimeError("HTTP 402 insufficient credit"), "Top up"),
])
def test_a_misconfigured_backend_still_stops_the_run(monkeypatch, error, expected_in_remedy):
    """Every stage would fail identically, and only a settings change helps."""
    _preflight_raising(monkeypatch, error)
    with pytest.raises(health.PreflightFailed) as caught:
        health.preflight()
    assert expected_in_remedy in caught.value.remedy


def test_a_model_that_cannot_return_json_still_stops_the_run(monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "prose/only")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: "I am happy to help!")
    with pytest.raises(health.PreflightFailed):
        health.preflight()


# --- the probe is quick ------------------------------------------------------

def test_the_probe_runs_against_a_short_budget(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "m")

    def capture(*a, **k):
        seen["budget"] = llm._total_budget()
        return {"ok": True}

    monkeypatch.setattr(llm, "call_json", capture)
    health.preflight()
    assert seen["budget"] == health.PROBE_BUDGET_SECONDS
    assert health.PROBE_BUDGET_SECONDS < llm.OPENAI_COMPATIBLE_TOTAL_BUDGET


def test_the_budget_override_is_restored_afterwards(monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "m")
    monkeypatch.setattr(llm, "call_json", lambda *a, **k: {"ok": True})
    health.preflight()
    assert llm._total_budget() == llm.OPENAI_COMPATIBLE_TOTAL_BUDGET


def test_the_override_is_restored_even_when_the_probe_raises(monkeypatch):
    _preflight_raising(monkeypatch, RATE_LIMITED)
    health.preflight()
    assert llm._total_budget() == llm.OPENAI_COMPATIBLE_TOTAL_BUDGET


# --- back off automatically instead of asking the operator to ----------------

def test_being_throttled_widens_the_gap_between_requests():
    """The error told the operator to lower LLM_CONCURRENCY by hand. The
    process has the evidence to do that itself."""
    assert llm.adaptive_gap() == 0.0
    llm._widen_spacing()
    first = llm.adaptive_gap()
    assert first > 0
    llm._widen_spacing()
    assert llm.adaptive_gap() > first


def test_the_gap_is_capped_so_a_run_cannot_crawl_to_a_halt():
    for _ in range(20):
        llm._widen_spacing()
    assert llm.adaptive_gap() == llm._ADAPTIVE_CEILING


def test_the_gap_never_narrows_inside_a_run():
    """A free tier's limit does not widen because we would like it to."""
    llm._widen_spacing()
    llm._widen_spacing()
    wide = llm.adaptive_gap()
    llm._throttle()
    assert llm.adaptive_gap() == wide


def test_each_run_starts_from_no_learned_spacing():
    """Starting every run at 12 seconds a request because an earlier one was
    refused would be its own slow failure."""
    import inspect

    from engine import pipeline

    llm._widen_spacing()
    assert llm.adaptive_gap() > 0
    assert "reset_adaptive_gap()" in inspect.getsource(pipeline.run_analysis)
    llm.reset_adaptive_gap()
    assert llm.adaptive_gap() == 0.0

"""A run died on someone else's 401 about a request that should not have been sent.

The live failure, in full: a report stopped with

    openai_compatible rejected the request: HTTP 401 from openrouter.ai
    {"error":{"message":"Missing Authentication header","code":401}}

and the page told the operator their API key had been rejected. It had not.
No key existed. The provider never received one, said so precisely, and the
engine translated "we got no credential" into "yours was refused" — sending
someone to check a value that was not there.

The engine had been able to answer this the whole time.
`Settings.configuration_problems()` says "LLM_API_KEY is required when
LLM_PROVIDER=openai_compatible". Nobody asked it. Two reasons, both pinned
here:

  1. `validate_for_startup()` — the fail-closed check that exists for exactly
     this — is registered on `engine/main.py`'s app, and the container runs
     `engine.api_server:app`. It has never executed in a deployment. A guard
     wired to a module nothing serves is not a guard; it is the appearance of
     one, which is worse, because it reads as proof the case is handled.
  2. Nothing reported configuration anywhere an operator could look, so "is
     my key set?" could only be answered by generating a whole report and
     reading how it died.

The rule, which is this codebase's own rule pointed at itself: **check what
you already know before asking a stranger.** A provider cannot have an
opinion about a variable it was never shown, and routing a diagnosis through
one can only ever describe the symptom.
"""

from pathlib import Path

import pytest

from engine import health
from engine.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _unconfigured() -> Settings:
    return Settings(llm_provider="openai_compatible", llm_base_url="",
                    llm_api_key="", llm_model="")


def test_the_config_check_can_report_without_raising():
    """A startup check wants to refuse; a health endpoint and a preflight
    want to report. The reporting half did not exist, which is why nothing
    could say what was missing without killing the container."""
    problems = _unconfigured().configuration_problems()
    assert any("LLM_API_KEY" in p for p in problems)
    # And the raising form still raises, for the caller that wants that.
    with pytest.raises(RuntimeError):
        _unconfigured().validate_for_startup()


def test_a_401_with_no_key_configured_is_reported_as_a_missing_variable(monkeypatch):
    """The live failure. OpenRouter answered 401 "Missing Authentication
    header" and the page said the key had been rejected — sending the
    operator to check a value that did not exist.

    The provider is not the authority on a variable it was never shown. Its
    401 is a consequence; the absent credential is the finding."""
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "deepseek/deepseek-v4-flash-0731")
    monkeypatch.setattr(llm, "missing_credentials", lambda: ["LLM_API_KEY"])
    monkeypatch.setattr(health, "_cached_probe", lambda *a, **k: None)

    def refuse(*_a, **_k):
        raise RuntimeError(
            'ProviderRejectedRequest: HTTP 401 from openrouter.ai — '
            '{"error":{"message":"Missing Authentication header","code":401}}')

    monkeypatch.setattr(llm, "call_json", refuse)

    with pytest.raises(health.PreflightFailed) as caught:
        health.preflight()

    remedy = caught.value.remedy
    assert "LLM_API_KEY" in remedy, "say the exact variable"
    assert "not set" in remedy, (
        "an absent variable is still being described as a rejected value")
    assert "rejected" not in remedy.lower().split("not set")[0], (
        "the operator is still told their key was refused")


def test_a_missing_key_does_not_explain_a_failure_that_is_not_about_auth(monkeypatch):
    """A credential gap explains a 401. It does not explain a retired model,
    and letting it try replaces a correct diagnosis with a plausible one —
    worse than none, because it reads as an answer."""
    from engine import llm

    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "stealth/ox-alpha")
    monkeypatch.setattr(llm, "missing_credentials", lambda: ["LLM_API_KEY"])
    monkeypatch.setattr(health, "_cached_probe", lambda *a, **k: None)

    def retired(*_a, **_k):
        raise RuntimeError("HTTP 404: Thank you for participating")

    monkeypatch.setattr(llm, "call_json", retired)

    with pytest.raises(health.PreflightFailed) as caught:
        health.preflight()
    assert "does not exist on this provider" in caught.value.remedy, (
        "a retired model is being reported as a credential problem")


def test_the_remedy_for_a_missing_variable_does_not_blame_the_provider():
    """A provider cannot have an opinion about a credential it was never
    shown, so its name has no place in the advice."""
    remedy = health._remedy_for_missing_configuration(
        ["LLM_API_KEY is required when LLM_PROVIDER=openai_compatible"],
        "openai_compatible")
    assert "no request was made" in remedy
    for word in ("rejected", "refused", "openrouter", "401"):
        assert word not in remedy.lower(), (
            f"the remedy mentions {word!r}, which describes a request that "
            "never happened")


def test_health_says_whether_a_credential_exists():
    """So the question is answerable from a URL instead of from a dead run."""
    from engine import api_server

    state = api_server._model_backend_state()
    assert "key_present" in state
    assert "key_variable" in state
    assert "production_grade" in state, (
        "an operator cannot see whether a report from this deployment would "
        "be sellable without generating one")


def test_health_never_prints_any_part_of_the_key(monkeypatch):
    """`/api/health` is public. A key is not less leaked for being leaked
    four characters at a time, so no prefix, no suffix, no length."""
    import engine.config as config_module
    from engine import api_server

    secret = "sk-or-v1-THISMUSTNEVERAPPEAR0123456789"
    monkeypatch.setattr(config_module.settings, "llm_provider", "openai_compatible")
    monkeypatch.setattr(config_module.settings, "llm_api_key", secret)

    rendered = repr(api_server._model_backend_state())
    assert secret not in rendered
    for size in (4, 6, 8):
        assert secret[:size] not in rendered, f"the first {size} characters leak"
        assert secret[-size:] not in rendered, f"the last {size} characters leak"
    assert str(len(secret)) not in rendered, "the key's length is reported"


def test_the_startup_guard_is_wired_to_the_app_that_is_actually_served():
    """`validate_for_startup()` sat on engine/main.py while the container
    ran engine.api_server:app, so the guard never fired in production. If a
    future change moves the served app, this says so."""
    start = (ROOT / "engine" / "start.sh").read_text()
    assert "engine.api_server:app" in start
    served = (ROOT / "engine" / "api_server.py").read_text()
    assert "_model_backend_state" in served, (
        "the served app has no way to report the configuration it is "
        "running under, which is how a container ran for days with no model "
        "credential and never said so")

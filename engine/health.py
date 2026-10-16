"""Is the model actually answering? Ask before doing three hours of work.

A live run produced a complete-looking report — narratives, sections, KPIs,
charts — in which every single LLM call had failed with HTTP 404, because the
configured model (`stealth/ox-alpha`, an OpenRouter cloaked preview) had been
retired. Narrative labelling fell back to keywords, scoring returned 0 of 109,
the analyst sections came back empty, and the page rendered all of it as though
it were a thin subject rather than a dead backend.

That is not a model problem. It is a design problem, and it is ours: the
pipeline has around 120 `except Exception` handlers, each individually correct
("a failed section must never break the run"), which together mean total
failure and thin data are indistinguishable in the output. The system could not
tell anyone it was broken.

Two mechanisms fix that, and neither is a heuristic:

  1. `preflight()` — one cheap real call before any work starts. If the model
     will not answer, stop immediately with the actual provider error and the
     specific thing to change. Three hours of collection and clustering to
     produce an empty report is worse than a ten-second failure.

  2. `RunHealth` — every LLM call reports success or failure while the run
     proceeds. A run that finishes with most calls failed is NOT a report, and
     is marked so that nothing downstream can present it as one.

The rule: a report may be thin because the subject is thin. It may never be
thin because the machine was broken and said nothing.
"""

from __future__ import annotations

import os
import threading
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field

# Above this share of failed calls, the run is not an analysis. Set where it is
# because a handful of failures is normal degradation (one oversized batch, one
# transient 429) while a majority means the backend is gone.
FAILURE_THRESHOLD = 0.5

#: How long the pre-run liveness probe may spend before giving its verdict.
#:
#: Must be at least one complete attempt, or the probe reports "the model did
#: not answer" about a provider it never gave time to answer — and the run
#: starts marked degraded on the strength of it. At 45s against a 180s attempt
#: timeout that is what it did to any model that thinks before replying.
#:
#: Derived rather than written down, so raising the attempt timeout can never
#: silently turn the probe back into a false negative. The cost is that a
#: genuinely dead backend takes one attempt to detect instead of 45s; the
#: probe is cached for ten minutes, so that is paid once per run at most.
def _probe_budget_seconds() -> float:
    from engine import llm

    return float(llm.OPENAI_COMPATIBLE_TIMEOUT)

VERDICT_OK = "ok"
VERDICT_DEGRADED = "degraded"
VERDICT_BROKEN = "broken"


@dataclass
class RunHealth:
    """LLM call accounting for one run. Thread-safe: stages run concurrently."""

    calls: int = 0
    failures: int = 0
    reasons: Counter = field(default_factory=Counter)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_success(self) -> None:
        with self._lock:
            self.calls += 1

    def record_failure(self, error: BaseException | str) -> None:
        label = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        with self._lock:
            self.calls += 1
            self.failures += 1
            self.reasons[label[:200]] += 1

    @property
    def failure_rate(self) -> float:
        return (self.failures / self.calls) if self.calls else 0.0

    @property
    def verdict(self) -> str:
        if not self.calls:
            return VERDICT_OK
        if self.failure_rate >= FAILURE_THRESHOLD:
            return VERDICT_BROKEN
        return VERDICT_DEGRADED if self.failures else VERDICT_OK

    @property
    def usable(self) -> bool:
        """False when the output must not be presented as an analysis."""
        return self.verdict != VERDICT_BROKEN

    def summary(self) -> dict:
        with self._lock:
            top = self.reasons.most_common(3)
        return {
            "calls": self.calls,
            "failures": self.failures,
            "failure_rate": round(self.failure_rate, 3),
            "verdict": self.verdict,
            "usable": self.usable,
            "top_errors": [{"error": reason, "count": count} for reason, count in top],
            "headline": self.headline(),
        }

    def headline(self) -> str | None:
        if self.verdict == VERDICT_OK:
            return None
        with self._lock:
            worst = self.reasons.most_common(1)
        detail = worst[0][0] if worst else "unknown error"
        if self.verdict == VERDICT_BROKEN:
            return (f"{self.failures} of {self.calls} model calls failed. This run is not "
                    f"an analysis — the sections below are empty because the model did not "
                    f"answer, not because there was nothing to find. First error: {detail}")
        return (f"{self.failures} of {self.calls} model calls failed; parts of this report "
                f"are incomplete. First error: {detail}")


_current = RunHealth()

# A thread bound to one run's tracker records into that run, not into
# whichever report happens to be current. See `engine.runctx`.
_bound = threading.local()


def current() -> RunHealth:
    tracker = getattr(_bound, "tracker", None)
    return tracker if tracker is not None else _current


@contextmanager
def bound(tracker: RunHealth):
    """Within this block, `current()` in THIS thread is `tracker`."""
    previous = getattr(_bound, "tracker", None)
    _bound.tracker = tracker
    try:
        yield tracker
    finally:
        _bound.tracker = previous


def reset() -> RunHealth:
    """Start accounting for a new run."""
    global _current
    _current = RunHealth()
    return _current


class PreflightFailed(RuntimeError):
    """The model will not answer. Raised before any work is done."""

    def __init__(self, message: str, remedy: str, error: str):
        super().__init__(message)
        self.remedy = remedy
        self.error = error

    def to_dict(self) -> dict:
        return {"error": str(self), "remedy": self.remedy, "provider_error": self.error}


def _dashboard() -> str:
    """The dashboard the operator is actually looking at.

    This said "the Render dashboard" for weeks after the move to Railway,
    and an operator with a dead report was sent to a product they do not
    use. It is the same failure as naming a menu that cannot exist: they
    go, do not find it, and conclude the advice is wrong — and then the
    next diagnosis starts from a false fact.

    Read from the environment rather than from a setting, because the host
    is the one thing a host always tells you about itself.
    """
    if any(key.startswith("RAILWAY_") for key in os.environ):
        return "the Railway dashboard (the service's Variables tab)"
    if os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"):
        return "the Render dashboard"
    if os.environ.get("FLY_APP_NAME"):
        return "fly.toml / `fly secrets set`"
    return "this service's environment variables"


def _key_variable(backend: str) -> str:
    """Which variable actually holds the key for this backend.

    Naming LLM_API_KEY to someone running the Anthropic path sends them to
    set a variable that is never read.
    """
    return "ANTHROPIC_API_KEY" if backend == "anthropic" else "LLM_API_KEY"


# Provider errors we can turn into an instruction instead of a stack trace.
def _remedy_for(error: str, model: str, backend: str) -> str:
    lowered = error.lower()
    dash = _dashboard()
    key = _key_variable(backend)
    if "404" in lowered or "not found" in lowered or "thank you for participating" in lowered:
        return (
            f"The model {model!r} does not exist on this provider any more. OpenRouter's "
            "cloaked/stealth previews (names like 'stealth/…') are temporary and are "
            "retired without notice — when one ends the endpoint returns 404 with "
            "'Thank you for participating'. Set LLM_MODEL to a current model id in "
            f"{dash} and redeploy."
        )
    # No key at all is NOT the same finding as a key that was refused, and
    # they have different remedies: one is a variable that was never set,
    # the other is a value that is wrong. Both arrive as HTTP 401, so the
    # generic branch below would have called an unset variable "rejected"
    # and sent the operator to check a key that does not exist.
    if ("missing authentication" in lowered or "no auth" in lowered
            or "missing bearer" in lowered or "no api key" in lowered):
        return (f"No API key was sent at all, so {key} is not set on this service "
                "— an empty value counts as unset. The provider never saw a key, "
                f"which is different from a key being wrong. Add {key} in {dash} "
                "and redeploy. Type it in there; never paste it into a chat.")
    if "401" in lowered or "unauthor" in lowered or "invalid api key" in lowered:
        return (f"The API key was rejected. The key in {key} reached the provider "
                f"and was refused, so it is wrong, revoked or expired. Set a valid "
                f"{key} in {dash} — type it in there, never paste it into a chat.")
    if "402" in lowered or "credit" in lowered or "quota" in lowered or "insufficient" in lowered:
        return "The account is out of credit for this model. Top up, or set LLM_MODEL to a cheaper one."
    if "429" in lowered or "rate" in lowered:
        return ("The provider is rate-limiting this key. Wait, lower LLM_CONCURRENCY, or "
                "use a model with more headroom.")
    if "timeout" in lowered or "connect" in lowered:
        return f"Could not reach the provider ({backend}). Check LLM_BASE_URL and outbound network access."
    return (f"The provider rejected a minimal request for model {model!r}. Check "
            f"LLM_MODEL, LLM_BASE_URL and {key} in {dash}.")


#: Failures that mean the backend is MISCONFIGURED. There is no point
#: collecting for an hour against a model that does not exist or a key that is
#: refused: every stage will fail identically, and the operator has to change a
#: setting before anything can work.
_FATAL_MARKERS = (
    "404", "not found", "thank you for participating",   # retired / wrong model
    "401", "unauthor", "invalid api key", "forbidden",   # bad key
    "402", "insufficient", "credit", "quota exceeded",   # no balance
    "model_not_found", "does not exist",
)

#: Failures that mean the backend is BUSY. A rate limit is not misconfiguration
#: — the same key that is throttled this second commonly serves the next call,
#: and a run that degrades visibly is worth far more than no run at all.
_TRANSIENT_MARKERS = ("429", "rate limit", "rate-limited", "too many requests",
                      "timeout", "timed out", "connection", "temporarily")


def _is_fatal(error: str) -> bool:
    """Misconfiguration, or just a busy provider?

    Blocking a whole run on a rate limit was too strict: it turned a report
    that would have come back thin into no report at all, and the operator
    could do nothing about it but wait. Only stop for the failures a setting
    can fix.
    """
    lowered = error.lower()
    if any(marker in lowered for marker in _FATAL_MARKERS):
        return True
    return not any(marker in lowered for marker in _TRANSIENT_MARKERS)


#: A successful probe is good for this long. The backend does not become
#: misconfigured between two runs a minute apart, and spending a request per
#: run to re-confirm that is a request not spent on the report — which matters
#: precisely when requests are scarce.
PROBE_CACHE_SECONDS = 600
_last_good_probe: tuple[float, dict] | None = None


def _cached_probe(backend: str, model: str) -> dict | None:
    """A recent success for THIS backend and model.

    Keyed on the configuration, not just time: a probe that passed against the
    old model says nothing about the new one, and serving that answer would
    hide exactly the misconfiguration this check exists to catch."""
    import time

    if _last_good_probe is None:
        return None
    when, result = _last_good_probe
    if time.monotonic() - when > PROBE_CACHE_SECONDS:
        return None
    if result.get("backend") != backend or result.get("model") != model:
        return None
    return {**result, "cached": True}


def reset_probe_cache() -> None:
    global _last_good_probe
    _last_good_probe = None


#: Errors that are about WHO is asking, as opposed to what was asked for.
#: A missing credential explains one of these. It does not explain a 404 for
#: a retired model or a 429 from a busy provider, and letting it try would
#: replace a correct diagnosis with a plausible one — which is worse than no
#: diagnosis, because it reads as an answer.
_AUTH_MARKERS = ("401", "403", "unauthor", "forbidden", "missing authentication",
                 "no api key", "invalid api key", "missing bearer",
                 "authentication_error", "api key")


def _looks_like_auth(error: str) -> bool:
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _AUTH_MARKERS)


def _remedy_for_missing_configuration(problems: list[str], backend: str) -> str:
    """What to set, where, said without involving the provider.

    Deliberately separate from `_remedy_for`: that one translates what a
    provider said, and a provider cannot have an opinion about a variable
    it was never shown.
    """
    dash = _dashboard()
    names = sorted({p.split()[0] for p in problems})  # tolerates either form
    listed = ", ".join(names)
    return (
        f"{listed} " + ("is" if len(names) == 1 else "are")
        + f" not set on this service, so no request was made. Add "
        + ("it" if len(names) == 1 else "them")
        + f" in {dash} and redeploy — type values in there, never paste them "
        "into a chat. If you believe you already set this, check you set it on "
        "THIS service and environment: a variable added after the running "
        "deployment was created does not reach it until the next deploy. "
        "`/api/health` reports `llm.key_present`, which answers it without "
        "generating a report."
    )


def preflight() -> dict:
    """One cheap real call before an expensive run.

    Raises PreflightFailed only when the backend is MISCONFIGURED — a retired
    model, a refused key, an exhausted balance — because every stage will then
    fail identically and only a settings change can help.

    A provider that is merely BUSY does not stop the run. The same key that is
    throttled now commonly serves the next call, the stage ledger already
    reports what failed, and a thin report beats no report.
    """
    from engine import llm

    backend = llm.provider()
    if backend == "stub":
        return {"ok": True, "backend": backend, "model": None, "note": "stub backend; no call made"}

    model = llm.bulk_model()
    cached = _cached_probe(backend, model)
    if cached is not None:
        return cached
    try:
        # A liveness probe on a tight leash. The full budget is 240s of
        # retrying, and spending that here to learn the provider is busy costs
        # a run four minutes before it has collected a single mention — while
        # the run itself could have been discovering the same thing usefully.
        with llm.short_budget(_probe_budget_seconds()):
            reply = llm.call_json(
                'Reply with ONLY this JSON and nothing else: {"ok": true, "n": 2}',
                max_tokens=llm.budget_for(200),
                model=model,
            )
    except Exception as exc:  # noqa: BLE001 — classified below
        error = f"{type(exc).__name__}: {exc}"[:500]
        # Explain the failure with what we know, not only with what the
        # provider said.
        #
        # A run died on "Missing Authentication header" and the page told
        # the operator their API key had been rejected. It had not: no key
        # existed, the provider never received one, and the engine could
        # have said so from its own configuration the whole time. A third
        # party can only ever describe the symptom — it has no opinion
        # about a variable it was never shown — so where a credential is
        # absent, that is the finding, and the provider's 401 is a
        # consequence of it rather than the cause.
        missing = llm.missing_credentials() if _looks_like_auth(error) else []
        if missing:
            raise PreflightFailed(
                "The model backend is not configured, so this run was "
                "stopped before it collected anything. Nothing was "
                "generated because nothing could be.",
                _remedy_for_missing_configuration(missing, backend),
                ", ".join(missing) + " not set — " + error,
            )
        if not _is_fatal(error):
            # Busy, not broken. Proceed, and say so loudly enough that a thin
            # report is not mistaken for a quiet subject.
            current().record_failure(exc)
            return {"ok": True, "backend": backend, "model": model,
                    "degraded": True,
                    "note": ("the model did not answer the preflight call but the run "
                             "continued — sections that cannot be produced will be "
                             "named individually"),
                    "error": error}
        raise PreflightFailed(
            f"The analysis model is not answering, so this run was stopped before it "
            f"collected anything. Nothing was generated because nothing could be.",
            remedy=_remedy_for(error, model, backend),
            error=error,
        ) from exc

    if not isinstance(reply, (dict, list)):
        raise PreflightFailed(
            "The model answered but not with JSON, so every structured stage would fail.",
            remedy=(f"Model {model!r} may not support JSON output reliably. Try a different "
                    "LLM_MODEL / LLM_BULK_MODEL."),
            error=f"got {type(reply).__name__}",
        )
    import time as _time

    global _last_good_probe
    result = {"ok": True, "backend": backend, "model": model}
    _last_good_probe = (_time.monotonic(), result)
    return result

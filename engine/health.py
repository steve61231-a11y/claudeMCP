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

import threading
from collections import Counter
from dataclasses import dataclass, field

# Above this share of failed calls, the run is not an analysis. Set where it is
# because a handful of failures is normal degradation (one oversized batch, one
# transient 429) while a majority means the backend is gone.
FAILURE_THRESHOLD = 0.5

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


def current() -> RunHealth:
    return _current


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


# Provider errors we can turn into an instruction instead of a stack trace.
def _remedy_for(error: str, model: str, backend: str) -> str:
    lowered = error.lower()
    if "404" in lowered or "not found" in lowered or "thank you for participating" in lowered:
        return (
            f"The model {model!r} does not exist on this provider any more. OpenRouter's "
            "cloaked/stealth previews (names like 'stealth/…') are temporary and are "
            "retired without notice — when one ends the endpoint returns 404 with "
            "'Thank you for participating'. Set LLM_MODEL to a current model id in the "
            "Render dashboard and redeploy."
        )
    if "401" in lowered or "unauthor" in lowered or "invalid api key" in lowered:
        return ("The API key was rejected. Set a valid LLM_API_KEY in the Render dashboard "
                "— type it into the dashboard, never paste it into a chat.")
    if "402" in lowered or "credit" in lowered or "quota" in lowered or "insufficient" in lowered:
        return "The account is out of credit for this model. Top up, or set LLM_MODEL to a cheaper one."
    if "429" in lowered or "rate" in lowered:
        return ("The provider is rate-limiting this key. Wait, lower LLM_CONCURRENCY, or "
                "use a model with more headroom.")
    if "timeout" in lowered or "connect" in lowered:
        return f"Could not reach the provider ({backend}). Check LLM_BASE_URL and outbound network access."
    return (f"The provider rejected a minimal request for model {model!r}. Check LLM_MODEL, "
            "LLM_BASE_URL and LLM_API_KEY in the Render dashboard.")


def preflight() -> dict:
    """One cheap real call. Raises PreflightFailed if the model will not answer.

    Deliberately trivial — a two-key JSON object — so it costs almost nothing
    and fails only when the backend genuinely cannot serve a request."""
    from engine import llm

    backend = llm.provider()
    if backend == "stub":
        return {"ok": True, "backend": backend, "model": None, "note": "stub backend; no call made"}

    model = llm.bulk_model()
    try:
        reply = llm.call_json(
            'Reply with ONLY this JSON and nothing else: {"ok": true, "n": 2}',
            max_tokens=200,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 — turned into an instruction below
        error = f"{type(exc).__name__}: {exc}"[:500]
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
    return {"ok": True, "backend": backend, "model": model}

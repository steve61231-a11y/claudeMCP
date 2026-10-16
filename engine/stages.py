"""A ledger of which report sections were produced, and which failed trying.

Four separate bugs in this system have had the same shape: a stage fails, its
exception is swallowed so the run survives, and the empty result renders as
though the corpus had nothing to say. Swallowing is correct — one dead analyst
must not cost a three-hour run — but swallowing SILENTLY is what makes a broken
section and a quiet subject identical on the page.

There are ~125 handlers of that shape. Patching them individually is endless.
This is the structural version: a stage that fails records why, the record
travels with the payload, and the frontend renders "this section failed" rather
than the absence the failure produced.

The distinction the ledger exists to preserve:

    empty  — the stage ran and found nothing. A finding.
    failed — the stage did not run. Not a finding, and never to be read as one.
"""

from __future__ import annotations

import threading
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
# Not run, on purpose, for a reason that is configuration rather than a fault:
# an account with no credits, a source switched off. Recorded so the operator
# can see why; never counted as a failure, because nothing failed.
STATUS_SKIPPED = "skipped"


@dataclass
class StageRecord:
    name: str
    status: str
    error: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict:
        out = {"stage": self.name, "status": self.status}
        if self.error:
            out["error"] = self.error
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class StageLedger:
    records: dict[str, StageRecord] = field(default_factory=dict)
    # Seconds each phase of the run took, in the order they happened. A run
    # that "takes too long" has to say WHERE, or every fix is a guess — and
    # this project has paid for guessing.
    timings: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_lap: float = field(default_factory=time.monotonic, repr=False)

    def lap(self, name: str) -> float:
        """Record the time since the previous lap (or since the ledger began)
        against `name`, and return it."""
        now = time.monotonic()
        with self._lock:
            seconds = round(now - self._last_lap, 1)
            self._last_lap = now
            self.timings[name] = round(self.timings.get(name, 0.0) + seconds, 1)
        return seconds

    def record(self, name: str, status: str, error: str | None = None,
               detail: str | None = None) -> None:
        with self._lock:
            self.records[name] = StageRecord(name, status, error, detail)

    def ok(self, name: str, detail: str | None = None) -> None:
        self.record(name, STATUS_OK, detail=detail)

    def empty(self, name: str, detail: str | None = None) -> None:
        """The stage ran and legitimately found nothing."""
        self.record(name, STATUS_EMPTY, detail=detail)

    def skipped(self, name: str, detail: str) -> None:
        """The stage did not run, deliberately — configuration, not a fault."""
        self.record(name, STATUS_SKIPPED, detail=detail)

    def failed(self, name: str, error: BaseException | str) -> None:
        """The stage did not run. Whatever is in its slot is not a result."""
        if isinstance(error, BaseException):
            where = ""
            # Skip this module's own frames: the ledger is never where the
            # bug is, and naming it hides the site that actually failed.
            frames = [line.strip() for line in traceback.format_exc().splitlines()
                      if line.strip().startswith('File "') and "/engine/" in line
                      and "/engine/stages.py" not in line]
            if frames:
                where = " @ " + frames[-1].split("/engine/")[-1]
            text = f"{type(error).__name__}: {error}{where}"
        else:
            text = str(error)
        self.record(name, STATUS_FAILED, error=text[:400])

    @property
    def failures(self) -> list[StageRecord]:
        with self._lock:
            return [r for r in self.records.values() if r.status == STATUS_FAILED]

    def summary(self) -> dict:
        with self._lock:
            records = list(self.records.values())
        failed = [r for r in records if r.status == STATUS_FAILED]
        with self._lock:
            timings = dict(self.timings)
        return {
            "stages": [r.to_dict() for r in sorted(records, key=lambda r: r.name)],
            "timings": timings,
            "failed": [r.name for r in failed],
            "failed_count": len(failed),
            "ok_count": sum(1 for r in records if r.status == STATUS_OK),
            "empty_count": sum(1 for r in records if r.status == STATUS_EMPTY),
            "skipped": [r.name for r in records if r.status == STATUS_SKIPPED],
            "headline": (
                f"{len(failed)} section(s) could not be produced: "
                f"{', '.join(r.name for r in failed[:6])}"
                f"{'…' if len(failed) > 6 else ''}. What they show is missing, not absent."
                if failed else None
            ),
        }


_current = StageLedger()

# A thread bound to one run's ledger sees that ledger rather than the
# process-wide one. See `engine.runctx` for why the back office needs this.
_bound = threading.local()


def current() -> StageLedger:
    ledger = getattr(_bound, "ledger", None)
    return ledger if ledger is not None else _current


@contextmanager
def bound(ledger: StageLedger):
    """Within this block, `current()` in THIS thread is `ledger`."""
    previous = getattr(_bound, "ledger", None)
    _bound.ledger = ledger
    try:
        yield ledger
    finally:
        _bound.ledger = previous


def reset() -> StageLedger:
    global _current
    _current = StageLedger()
    return _current


def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict, tuple, set, str)):
        return len(value) == 0
    return False


@contextmanager
def guard(name: str, fallback=None):
    """Run a stage, recording what happened to it.

    Yields a one-key box; assign the stage's result to `box["value"]`. On an
    exception the fallback is kept AND the failure is recorded, so the empty
    slot downstream carries the reason it is empty.

        with stages.guard("public_voice", fallback=[]) as box:
            box["value"] = analysts.analyze_public_voice(name, mentions)
        result = box["value"]
    """
    box = {"value": fallback}
    try:
        yield box
    except Exception as exc:  # noqa: BLE001 — recorded, not hidden
        current().failed(name, exc)
        box["value"] = fallback
        return
    if _is_empty(box["value"]):
        current().empty(name)
    else:
        current().ok(name)


def run_guarded(name: str, fn, fallback=None):
    """`guard` as a call. Returns the stage's result, or the fallback."""
    with guard(name, fallback=fallback) as box:
        box["value"] = fn()
    return box["value"]

"""Which run a thread is working for.

The stage ledger and the run-health tracker are process-wide and replaced at
the start of every report. That was safe while a run's last stage finished
before the next run could begin. It stopped being safe the moment the back
office moved to after delivery: a reader can press Generate again while the
previous report's claim audit is still running, and the audit would then
record its stages and its model calls into the NEW run's ledger — a failure
from one report shown on another, which is this codebase's founding rule
broken across two reports instead of two sections.

So a thread can be bound to a particular run's ledger and tracker, and
`carry` takes that binding into a worker thread. Unbound threads see the
process-wide objects exactly as before.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps

from engine import health, stages


@contextmanager
def run_context(ledger, tracker):
    """Bind this thread to one run's ledger and health tracker."""
    with stages.bound(ledger), health.bound(tracker):
        yield


def carry(fn):
    """`fn`, run in whatever thread, against the CALLER's run.

    Thread pools do not inherit thread-local state, so a worker submitted
    from a bound thread would otherwise fall back to the process-wide
    objects — which, by then, may belong to someone else's report.
    """
    ledger, tracker = stages.current(), health.current()

    @wraps(fn)
    def wrapper(*args, **kwargs):
        with run_context(ledger, tracker):
            return fn(*args, **kwargs)

    return wrapper

"""Eleven sections, then nothing, for hours.

A live run delivered eleven of fifteen sections and stopped: the executive
brief spinning, and every stage after it — including the client's Sentiment
Framework, which pipeline.py builds later — never reached. "Still building
this report" for hours, because nothing was going to arrive.

The analyst fan-out has had a deadline for a while, and its comment says
exactly why: "without one the report never finishes". The two stages that run
AFTER the fan-out — the executive brief and grounding verification — were left
outside it, sequential and unbounded. `stages.run_guarded` wraps them, but a
guard catches exceptions and a call that never returns raises none; waiting
inside a try block is still waiting.
"""

import time

import pytest

from engine.reports import sections


def test_a_stage_that_never_returns_is_abandoned_not_awaited():
    started = time.monotonic()
    result = sections._bounded("hanging_stage", 1.0,
                               lambda: time.sleep(30), fallback="fallback")
    elapsed = time.monotonic() - started
    assert result == "fallback"
    assert elapsed < 5, f"waited {elapsed:.1f}s on a stage with a 1s deadline"


def test_the_abandoned_stage_is_recorded_not_silently_dropped():
    """A section that vanishes without trace is indistinguishable from a
    section the corpus could not support."""
    from engine import stages

    stages.reset()
    sections._bounded("hanging_stage", 0.5, lambda: time.sleep(30), fallback=None)
    summary = stages.current().summary()
    assert "hanging_stage" in str(summary), "the abandoned stage left no record"


def test_a_stage_that_finishes_in_time_returns_its_real_value():
    assert sections._bounded("quick", 5.0, lambda: "real", fallback="fallback") == "real"


def test_a_stage_that_raises_still_falls_back():
    """The exception path run_guarded already handled must keep working."""
    def boom():
        raise RuntimeError("provider said no")
    assert sections._bounded("boom", 5.0, boom, fallback="fallback") == "fallback"


def test_the_tail_uses_the_same_deadline_as_the_fan_out():
    """Two different budgets for the same run is a second number to forget to
    update."""
    import inspect

    source = inspect.getsource(sections)
    # The call spans two lines; what matters is that both go through the
    # bounded helper and are handed the fan-out's own deadline.
    assert '_bounded(\n            "executive_brief", deadline,' in source \
        or '_bounded("executive_brief", deadline' in source
    assert '_bounded("grounding_verification", deadline' in source

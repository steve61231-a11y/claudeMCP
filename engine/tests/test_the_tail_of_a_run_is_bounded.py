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


# --- one budget for the phase, not one per stage -----------------------------

def test_the_tail_shares_the_phase_budget_rather_than_restarting_it():
    """The first version of this fix bounded the fan-out and then handed the
    two stages after it the SAME full deadline each, making the worst case
    three times the budget — 45 minutes rather than 15. Bounded, but so
    loosely that a reader still sat on "Still building this report" long past
    the point of usefulness."""
    import inspect

    source = inspect.getsource(sections)
    assert "def remaining()" in source, "no shared budget for the phase"
    assert "as_completed(futures, timeout=remaining())" in source
    assert '_bounded("grounding_verification", remaining()' in source
    assert '"executive_brief", remaining(),' in source


def test_the_whole_run_has_a_ceiling_above_the_analyst_phase():
    """The analyst phase is bounded; the heavy blocks after it — resolution,
    knowledge graph, temporal signals, sentiment framework, verification —
    were not, and each makes model calls. A live run reached 12/15 and stayed
    there: not failing, just never coming back."""
    from engine.config import settings

    assert settings.report_deadline_seconds > settings.analyst_deadline_seconds


def test_every_unbounded_heavy_block_is_gated_on_the_run_ceiling():
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline.run_analysis)
    for stage in ("entity_event_resolution", "sentiment_framework", "claim_verification"):
        assert f'out_of_time("{stage}")' in source, f"{stage} can still overrun the run"


def test_a_skipped_stage_says_it_ran_out_of_time():
    """Silently absent, a skipped stage is indistinguishable from one the
    corpus could not support — the defect behind four separate bugs already."""
    import inspect

    from engine import pipeline

    source = inspect.getsource(pipeline.run_analysis)
    assert "skipped: the run passed its" in source

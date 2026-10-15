"""Thirty minutes, and the smallest pool was on the biggest stage.

The reader's note was "30 minutes is a long time, I want it as quick as
possible". The ceiling is not the problem — lowering it would only truncate
more sections. The problem was arithmetic.

Sentiment scoring walks every unscored mention in the window, 25 to a
batch, and ran them through FOUR workers: the narrowest pool anywhere in
the pipeline, on the stage with the most items. A 1,500-mention backlog is
sixty batches — fifteen sequential waves — against a 150-second deadline.
It could not finish. The remainder stayed unscored, unscored mentions carry
into the next run, and so the backlog grew on every report while the stage
got further behind itself.

Meanwhile the analyst fan-out ran fifteen sections through eight workers:
two waves, and the second wave is wall-clock the reader waits through for
nothing.

Every one of these threads is blocked on an HTTP response, not allocating —
the correction `low_memory` already received, applied to the pools that
were still narrow. Widening them is latency, not memory. `LLM_MAX_CONCURRENCY`
is the limit that actually bounds spend, it is process-wide, and it is
enforced in `llm.call_json`, so none of this can run away from an operator
who has set one.
"""

from engine.agents.score import _BATCH_WORKERS, settings
from engine.config import settings as config_settings
from engine.reports.digest import MAP_WORKERS


def test_the_busiest_stage_is_not_the_narrowest_pool():
    """Scoring walks more items than any other model stage. It must not be
    the one given the fewest workers."""
    assert _BATCH_WORKERS >= MAP_WORKERS, (
        f"scoring runs {_BATCH_WORKERS} batches at once against the digest's "
        f"{MAP_WORKERS}, and scoring walks far more items")
    assert _BATCH_WORKERS >= 12


def test_the_analyst_fan_out_fits_the_sections_in_one_wave():
    """Fifteen sections through eight workers is two waves. The second one
    is latency with nothing to show for it."""
    assert config_settings.llm_fanout_workers >= 15, (
        "the analyst fan-out still runs in more than one wave for a normal "
        "report")


def test_collectors_are_wide_enough_to_overlap_their_waiting():
    """They are I/O-bound and mostly on different hosts, so width overlaps
    the waiting rather than multiplying the load on any one source."""
    assert config_settings.ingestion_workers >= 12


def test_the_polite_gap_still_applies_whatever_the_width():
    """Widening the pool must not make this engine a worse guest. GDELT
    meters ~1 request / 5s per IP and the whole service is one IP — the
    per-host gap is what keeps the news backbone working at all."""
    from engine.ingestion import http

    assert http.HOST_MIN_INTERVAL["api.gdeltproject.org"] >= 5.0
    assert http.HOST_MIN_INTERVAL["new.kenyalaw.org"] >= 2.0


def test_an_operator_can_still_cap_the_whole_process():
    """Width is latency; spend is bounded elsewhere, and that lever has to
    keep working or widening the pools is a bill nobody agreed to."""
    from engine import llm

    assert llm.concurrency(_BATCH_WORKERS) == _BATCH_WORKERS

    original = config_settings.llm_max_concurrency
    try:
        config_settings.llm_max_concurrency = 3
        assert llm.concurrency(_BATCH_WORKERS) == 3, (
            "LLM_MAX_CONCURRENCY no longer clamps a widened pool")
        assert llm.concurrency(config_settings.llm_fanout_workers) == 3
    finally:
        config_settings.llm_max_concurrency = original

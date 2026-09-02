"""The report window, and what has to arrive before the slow stages.

Two complaints, one root each:

  - "how far back are we looking?" It was 210 days, hard-coded — seven months,
    so every report was dominated by material half a year old and "what is
    happening now" was diluted by everything that ever happened.

  - "since your last report, sentiment over time and where this data came from
    never finish." They sat BELOW the analyst fan-out in the pipeline, so they
    waited on the slowest LLM stage in the system despite making no model call
    at all.
"""

import inspect
import re

import pytest

from engine.config import Settings, settings


# --- the window --------------------------------------------------------------

def test_the_default_window_is_a_month_not_seven():
    assert Settings().report_window_days == 30


def test_the_window_is_no_longer_hard_coded():
    from engine import api_server

    source = inspect.getsource(api_server._run_report_job)
    assert "timedelta(days=210)" not in source
    assert "window_days" in source


def test_a_caller_may_widen_the_window():
    from engine.api_server import ReportRequest

    assert "days" in ReportRequest.model_fields


@pytest.mark.parametrize("requested,expected", [
    (None, 30),      # unset -> the default month
    (0, 30),         # 0 is not a window; treat it as unset rather than as a day
    (14, 14), (365, 365),
    (-5, 1),         # nonsense, but never zero or negative days of corpus
    (99999, 730),    # clamped to the ceiling
])
def test_the_requested_window_is_clamped(requested, expected):
    clamped = max(1, min(int(requested or settings.report_window_days),
                         settings.report_window_max_days))
    assert clamped == expected


def test_the_ceiling_stops_a_run_reading_years_of_corpus():
    assert Settings().report_window_max_days <= 730


# --- ordering: nothing free waits on something expensive --------------------

FREE_SECTIONS = ["data_coverage", "data_provenance", "evidence_gate",
                 "since_last_report", "sentiment_history"]


def _pipeline_source():
    from engine import pipeline

    return inspect.getsource(pipeline.run_analysis)


@pytest.mark.parametrize("section", FREE_SECTIONS)
def test_free_sections_are_published_before_the_analyst_fan_out(section):
    """Each of these is arithmetic or a database query. None needs a model, and
    none of them may sit behind one."""
    source = _pipeline_source()
    publish_at = source.index(f'publish("{section}"')
    fanout_at = source.index("payload = enrich_report_payload(")
    assert publish_at < fanout_at, (
        f"{section} is published AFTER the analysts, so a slow analyst withholds "
        "a section that costs nothing to produce")


def test_the_fan_out_still_happens():
    assert "enrich_report_payload(" in _pipeline_source()


# --- the two analysts that hung are split -----------------------------------

def test_public_voice_runs_one_call_per_stance():
    """Three stances x 4-8 themes x 150 words x 6 quotes in ONE reply is
    3,000-5,000 words. On a model that reasons first, that reply is cut off,
    the budget ladder climbs, it is cut off again, and the section never lands."""
    from engine.reports import analysts

    source = inspect.getsource(analysts.analyze_public_voice)
    assert "supportive" in source and "critical" in source and "neutral" in source
    assert "ThreadPoolExecutor" in source, "the stances must run in parallel"
    assert "_public_voice_stance" in source


def test_a_failed_stance_costs_a_third_not_the_section(monkeypatch):
    from engine.reports import analysts

    calls = {"n": 0}

    def flaky(instructions, untrusted, expected_keys, max_tokens,
              max_untrusted_chars, model=None):
        calls["n"] += 1
        if "critical" in instructions:
            raise RuntimeError("cut off")
        return {"themes": [{"theme": "t", "summary": "s", "quotes": []}]}

    monkeypatch.setattr(analysts.llm, "call_json_untrusted", flaky)
    voice = analysts.analyze_public_voice("X", [])
    # A small corpus tries ONE combined call first — three requests is waste on
    # a throttled key — and splits only when that does not work. So: the
    # combined attempt, then one call per stance.
    assert calls["n"] == 4, "combined attempt, then one call per stance"
    assert voice["supportive"] and voice["neutral"]
    assert voice["critical"] == [], "the failed stance is empty, the others survive"


def test_the_timeline_is_split_across_the_window():
    from engine.reports import analysts

    source = inspect.getsource(analysts.analyze_timeline)
    assert "_timeline_slice" in source
    assert "halves" in source


def test_every_analyst_call_carries_reasoning_headroom():
    """ANALYST_MAX_TOKENS was passed raw. On a thinking model that budget is
    spent before the answer starts."""
    from engine.reports import analysts

    source = inspect.getsource(analysts)
    bare = re.findall(r"max_tokens=ANALYST_MAX_TOKENS\b", source)
    assert not bare, f"{len(bare)} analyst call(s) still ask for an output-only budget"


# --- the deadline is short enough to be a deadline --------------------------

def test_the_analyst_deadline_is_minutes_not_a_quarter_hour():
    assert Settings().analyst_deadline_seconds <= 600

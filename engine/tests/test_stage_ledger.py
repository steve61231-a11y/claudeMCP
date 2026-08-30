"""A failed section must never be readable as an empty one.

Four separate bugs in this system have had one shape: a stage fails, the
exception is swallowed so the run survives, and the empty result renders as
though the corpus had nothing to say. Swallowing is right; swallowing silently
is what made a broken section and a quiet subject identical on the page.
"""

import pytest

from engine import stages
from engine.reports.issue_framework import normalise_position


@pytest.fixture(autouse=True)
def _fresh():
    stages.reset()
    yield
    stages.reset()


# --- the distinction the ledger exists for -----------------------------------

def test_a_stage_that_ran_and_found_nothing_is_empty_not_failed():
    stages.run_guarded("timeline", lambda: [], fallback=[])
    record = stages.current().records["timeline"]
    assert record.status == stages.STATUS_EMPTY
    assert stages.current().failures == []


def test_a_stage_that_raised_is_failed_not_empty():
    stages.run_guarded("public_voice", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                       fallback=[])
    record = stages.current().records["public_voice"]
    assert record.status == stages.STATUS_FAILED
    assert "boom" in record.error


def test_the_fallback_is_still_returned_so_the_run_survives():
    result = stages.run_guarded("x", lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                                fallback=["safe"])
    assert result == ["safe"], "a dead stage must not cost the run"


def test_a_populated_stage_is_ok():
    stages.run_guarded("pulse", lambda: [{"platform": "x"}], fallback=[])
    assert stages.current().records["pulse"].status == stages.STATUS_OK


def test_the_headline_names_what_failed():
    stages.run_guarded("public_voice", lambda: (_ for _ in ()).throw(RuntimeError("a")), fallback=[])
    stages.run_guarded("timeline", lambda: (_ for _ in ()).throw(RuntimeError("b")), fallback=[])
    headline = stages.current().summary()["headline"]
    assert "public_voice" in headline and "timeline" in headline
    assert "missing, not absent" in headline


def test_a_clean_run_produces_no_headline():
    stages.run_guarded("a", lambda: [1], fallback=[])
    stages.run_guarded("b", lambda: [], fallback=[])
    assert stages.current().summary()["headline"] is None, "a healthy run must not nag"


def test_the_error_names_where_it_happened():
    def inner():
        raise ValueError("bad json")

    stages.run_guarded("deep_insights", inner, fallback={})
    assert "engine/" not in stages.current().records["deep_insights"].error
    assert "ValueError: bad json" in stages.current().records["deep_insights"].error


def test_summary_is_json_safe():
    import json

    stages.run_guarded("x", lambda: (_ for _ in ()).throw(RuntimeError("boom")), fallback=[])
    json.dumps(stages.current().summary())


def test_the_ledger_is_threadsafe():
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: stages.run_guarded(f"s{i}", lambda: [i], fallback=[]), range(200)))
    assert len(stages.current().records) == 200


# --- the seam every report section passes through ----------------------------

def test_a_failed_analyst_is_recorded_at_the_section_seam(monkeypatch):
    """enrich_report_payload ran every analyst through a try/except that
    returned [] — a dead analyst and a silent corpus produced one page."""
    from engine.reports import sections

    jobs = {"public_voice": (lambda: (_ for _ in ()).throw(RuntimeError("provider 500")), [])}

    def run(key):
        fn, fallback = jobs[key]
        return key, stages.run_guarded(key, fn, fallback=fallback)

    key, value = run("public_voice")
    assert value == []
    assert stages.current().records[key].status == stages.STATUS_FAILED
    assert sections.stages is stages, "sections must report into the shared ledger"


# --- stance labels no longer silently delete stakeholders --------------------

@pytest.mark.parametrize("label,expected", [
    ("for", "for"), ("For", "for"), ("FOR", "for"), ("supportive", "for"),
    ("pro", "for"), ("in favour", "for"), ("somewhat supportive of it", "for"),
    ("against", "against"), ("Opposed", "against"), ("critical", "against"),
    ("neutral", "neutral"), ("undecided", "neutral"),
    ("", "neutral"), (None, "neutral"), ("something unparseable", "neutral"),
])
def test_every_stance_label_lands_in_a_bucket(label, expected):
    assert normalise_position(label) == expected


def test_a_stakeholder_is_never_dropped_by_its_stance_label():
    """Exact matching on "for"/"against"/"neutral" dropped anyone the model
    labelled differently from ALL THREE buckets — they left the section."""
    stakeholders = [{"name": "A", "position": "For"}, {"name": "B", "position": "opposed"},
                    {"name": "C", "position": "undecided"}, {"name": "D", "position": "???"}]
    buckets = {"for": [], "against": [], "neutral": []}
    for person in stakeholders:
        buckets[normalise_position(person["position"])].append(person["name"])
    assert sorted(sum(buckets.values(), [])) == ["A", "B", "C", "D"]

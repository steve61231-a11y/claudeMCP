"""Guard against the bug shape that has cost this project the most time.

Every serious defect found in the report pipeline so far has been the same
thing wearing different clothes:

  - the spam filter deleted all news (100 items in, 2 stored)
  - narrative labelling failed and emitted "narrative-3"
  - quote validation rejected a straightened apostrophe and deleted the theme
  - a retired model 404'd and every section rendered empty
  - a failed analyst returned [] at the section seam
  - a stakeholder labelled "For" vanished from all three buckets

In each case content was destroyed or never produced, and the output was
indistinguishable from a subject nobody was talking about. These tests assert
the invariants that make that shape detectable rather than silent.
"""

import ast
import pathlib

import pytest

from engine import health, stages

ENGINE = pathlib.Path(__file__).resolve().parent.parent

# Modules that turn a corpus into report content. A swallowed failure here is
# what a reader sees as an empty section, so each must report into the ledger.
CONTENT_MODULES = [
    "reports/sections.py",
    "reports/analysts.py",
    "intelligence/narratives.py",
    "pipeline.py",
]


@pytest.mark.parametrize("relative", CONTENT_MODULES)
def test_content_modules_report_their_failures(relative):
    source = (ENGINE / relative).read_text()
    assert "stages" in source, (
        f"{relative} produces report content but never reports a stage outcome; "
        "a failure there is invisible to the reader")


def test_the_section_seam_does_not_swallow_bare():
    """enrich_report_payload's `run` is the single seam every analyst section
    passes through. It must not return a fallback without recording why."""
    source = (ENGINE / "reports/sections.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            body = ast.dump(node)
            assert "run_guarded" in body, (
                "the section seam returned the fallback silently — a dead analyst "
                "and a silent corpus produced the same page")
            return
    pytest.fail("the `run` seam in sections.py has moved; re-point this guard")


def test_a_broken_run_cannot_report_itself_as_usable():
    tracker = health.RunHealth()
    for _ in range(50):
        tracker.record_failure(RuntimeError("HTTP 404"))
    assert not tracker.usable
    assert tracker.headline()


def test_an_empty_stage_and_a_failed_stage_are_distinguishable():
    ledger = stages.StageLedger()
    ledger.empty("found_nothing")
    ledger.failed("could_not_run", RuntimeError("boom"))
    summary = ledger.summary()
    assert summary["failed"] == ["could_not_run"]
    assert summary["empty_count"] == 1
    assert "found_nothing" not in summary["failed"], (
        "a stage that ran and found nothing is a FINDING, not a failure")


def test_publisher_source_types_are_still_exempt_from_the_burst_rule():
    """The spam filter once discarded every news article because an outlet
    publishing sixteen stories looked like a flooding account."""
    from engine.processing.cleaning import PUBLISHER_SOURCE_TYPES, clean_mentions
    from datetime import datetime

    assert {"article", "video", "reference"} <= PUBLISHER_SOURCE_TYPES
    articles = [{"platform": "nation.africa", "source_type": "article",
                 "author_handle": "nation.africa", "text": f"Distinct story number {i}.",
                 "posted_at": datetime(2026, 7, 1), "engagement": {}, "raw_payload": {}}
                for i in range(30)]
    cleaned = clean_mentions(articles)
    assert sum(1 for m in cleaned if not m["is_spam"]) == 30


def test_narratives_can_never_emit_a_numbered_placeholder():
    from engine.intelligence import narratives

    for placeholder in ("narrative-3", "Cluster 12", "topic_7", "theme 1", ""):
        assert narratives._looks_useless(placeholder)
    assert not narratives._looks_useless("Kitale mega rally")


def test_a_faithful_quote_survives_validation():
    from engine.reports.analysts import quote_is_grounded

    source = "Sifuna’s remarks didn’t go down well — “We won’t accept it,” he said."
    assert quote_is_grounded('"We won\'t accept it," he said.', source)
    assert not quote_is_grounded("Sifuna welcomed the proposal warmly", source)

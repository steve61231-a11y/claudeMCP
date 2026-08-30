"""A check that did not run must never read as a check that passed.

The contradiction search returning "nothing contradicts this" is a claim ABOUT
THE CORPUS. When the search fails, the finding kept its HIGH confidence and the
reason "…with nothing contradicting" — an assertion about evidence nobody had
looked at, attached to a claim we were about to publish.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from engine import stages
from engine.evidence import findings as fm
from engine.evidence import records as rm


@pytest.fixture(autouse=True)
def _fresh():
    stages.reset()
    yield
    stages.reset()


def _corpus(n=5, topic="cost of living"):
    records = [rm.EvidenceRecord(
        mention_id=f"m{i}", kind=rm.KIND_FACT, status=rm.STATUS_REPORTED,
        statement=f"Statement {i} about {topic}.", topic=topic, platform=f"p{i}",
        posted_at=datetime(2026, 7, i + 1).isoformat()) for i in range(n)]
    mentions = [{"id": f"m{i}", "text": f"Genuinely distinct story number {i} " * 8,
                 "platform": f"p{i}", "author_handle": "a",
                 "posted_at": datetime(2026, 7, i + 1), "engagement": {}} for i in range(n)]
    return records, mentions


def _build(call_json, **kw):
    records, mentions = _corpus()
    with patch.object(fm.llm, "call_json", **({"side_effect": call_json}
                                              if isinstance(call_json, Exception)
                                              else {"new": call_json})), \
         patch.object(fm, "challenge", lambda f: {"verdict": "PASS", "reason": "ok"}):
        return fm.build_findings(records, mentions, review=True, **kw)[0]


def test_a_failed_search_cannot_leave_a_finding_at_high_confidence():
    finding = _build(RuntimeError("provider down"))
    assert finding.confidence == fm.CONFIDENCE_MEDIUM
    assert finding.contradiction_checked is False


def test_the_reason_stops_claiming_nothing_contradicts_it():
    finding = _build(RuntimeError("provider down"))
    assert "with nothing contradicting" not in finding.confidence_reason
    assert "did not run" in finding.confidence_reason


def test_the_failed_search_is_named_in_the_ledger():
    _build(RuntimeError("provider down"))
    assert any(r.name == "contradiction_search" for r in stages.current().failures)


def test_a_search_that_ran_and_found_nothing_keeps_its_confidence():
    """Searched-and-clean is a real finding, and must not be penalised."""
    finding = _build(lambda *a, **k: {"contradicting": [], "open_questions": []})
    assert finding.confidence == fm.CONFIDENCE_HIGH
    assert finding.contradiction_checked is True
    assert "with nothing contradicting" in finding.confidence_reason


def test_an_empty_pool_counts_as_not_searched():
    assert fm.find_contradictions("a claim", []) == ([], [], False)


def test_a_successful_search_reports_that_it_ran():
    with patch.object(fm.llm, "call_json",
                      return_value={"contradicting": [1], "open_questions": ["q?"]}):
        indices, questions, searched = fm.find_contradictions(
            "claim", [rm.EvidenceRecord(mention_id="m", kind="fact",
                                        status="reported", statement="s")])
    assert searched is True and indices == [0] and questions == ["q?"]


# --- a failed sceptic pass is already handled; pin it ------------------------

def test_an_unreviewed_finding_is_never_labelled_pass():
    with patch.object(fm.llm, "call_json", side_effect=RuntimeError("down")):
        assert fm.challenge(fm.Finding(title="t"))["verdict"] == "NOT_REVIEWED"

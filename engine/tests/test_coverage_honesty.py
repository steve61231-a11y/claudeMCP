"""The corpus-coverage number must not lie, and no stage may vanish silently.

The report said "Read by the analyst: 188 · every item read" on a run where
every model call returned HTTP 404. `_digest_chunk` returned {} on failure but
still stamped `_mentions_in_chunk = len(chunk)`, so the coverage record counted
mentions in FAILED chunks as analysed and `complete` came out true.

That is the most damaging thing this pipeline could do: it is the number that
makes the thin sections beneath it look like an honest reading of the corpus
rather than the residue of a dead backend.
"""

from unittest.mock import patch

import pytest

from engine import stages
from engine.reports import digest


@pytest.fixture(autouse=True)
def _fresh():
    stages.reset()
    yield
    stages.reset()


def _mentions(n, words=60):
    return [{"id": f"m{i}", "text": "word " * words, "platform": "x",
             "source_type": "post", "author_handle": "a", "posted_at": None,
             "engagement": {}} for i in range(n)]


def _run(side_effect):
    # A corpus that FITS in one analyst window is now sent whole and never
    # compressed — correct, and covered in test_digest_is_not_a_chokepoint.py.
    # These tests are about the map step, so the window is shrunk to force the
    # corpus through it. Without this they assert about a step that no longer
    # runs, and pass or fail for the wrong reason.
    with patch.object(digest, "DIGEST_CONTEXT_CHARS", 4000), \
         patch.object(digest.llm, "call_json", side_effect=side_effect), \
         patch.object(digest.llm, "bulk_model", lambda: "m"), \
         patch.object(digest.llm, "concurrency", lambda n: 1):
        return digest.build_corpus_digest("Subject", _mentions(60))


# --- the lie -----------------------------------------------------------------

def test_a_total_failure_reports_nothing_read_not_everything_read():
    result = _run(RuntimeError("HTTP 404 model not found"))
    coverage = result["coverage"]
    assert coverage["mentions_analyzed"] == 0, "failed chunks were counted as read"
    assert coverage["complete"] is False
    assert coverage["chunks_failed"] == coverage["chunks"]


def test_the_coverage_record_says_how_many_mentions_were_never_read():
    note = _run(RuntimeError("HTTP 404"))["coverage"]["note"]
    assert "60 mentions were never read" in note
    assert "passes over the corpus failed" in note


def test_a_partial_failure_counts_only_what_was_actually_read():
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"digest": {"claims": [{"ref": "m0", "text": "a claim"}]}}
        raise RuntimeError("HTTP 429")

    result = _run(flaky)
    coverage = result["coverage"]
    assert 0 < coverage["mentions_analyzed"] < 60
    assert coverage["chunks_failed"] == coverage["chunks"] - 1
    assert coverage["complete"] is False


def test_a_clean_run_still_reports_complete():
    result = _run(lambda *a, **k: {"digest": {"claims": []}})
    coverage = result["coverage"]
    assert coverage["mentions_analyzed"] == 60
    assert coverage["chunks_failed"] == 0
    assert coverage["complete"] is True
    assert "note" not in coverage


def test_every_failed_chunk_is_named_in_the_ledger():
    result = _run(RuntimeError("HTTP 404"))
    failed = [r.name for r in stages.current().failures]
    chunks = [name for name in failed if name.startswith("digest_chunk:")]
    assert len(chunks) == result["coverage"]["chunks"], "a failed chunk went unnamed"
    # A run where EVERY pass failed also records the step itself, so the reader
    # is told the corpus was never compressed rather than having to infer it
    # from a list of chunk failures.
    assert "digest" in failed


# --- a failed call must not become a scored reading --------------------------

def test_a_failed_sentiment_call_is_not_recorded_as_neutral():
    """Neutral is a reading. An unanswered request is not, and counting one as
    the other inflates the neutral share with data never produced."""
    from engine.processing import sentiment

    with patch.object(sentiment.llm, "call_json_untrusted",
                      side_effect=RuntimeError("provider down")):
        result = sentiment.llm_sentiment_and_context("some text")
    assert result["scored"] is False
    assert result["sentiment"] is None, "a failed call must not read as neutral"


def test_a_successful_sentiment_call_is_marked_scored():
    from engine.processing import sentiment

    with patch.object(sentiment.llm, "call_json_untrusted",
                      return_value={"sentiment": "negative", "intensity": 4}):
        result = sentiment.llm_sentiment_and_context("some text")
    assert result["scored"] is True and result["sentiment"] == "negative"


# --- verification that never ran is not a report with nothing to check -------

def test_nothing_to_check_is_distinguished_from_the_checker_being_down():
    from engine.agents import verify

    stages.current().failed("claim_extraction[4]", RuntimeError("HTTP 404"))
    with patch.object(verify, "extract_claims_batch", return_value={}):
        result = verify.verify_payload(None, None, {"executive_summary": "Some prose."})
    assert "claim extraction failed" in result["note"]


def test_a_report_with_no_assertions_says_so_plainly():
    from engine.agents import verify

    with patch.object(verify, "extract_claims_batch", return_value={}):
        result = verify.verify_payload(None, None, {"executive_summary": "Some prose."})
    assert "no checkable factual assertions" in result["note"]

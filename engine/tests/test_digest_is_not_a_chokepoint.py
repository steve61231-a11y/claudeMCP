"""The compression step was a single point of failure in front of everything.

A run collected 23 documents and reported "Analysed 0 of 23". Every one of them
was sitting in memory. The map step — which exists to COMPRESS a corpus too
large to send — failed on every chunk, and the analysts were handed an empty
digest and produced nothing, so the framework built on top of them rendered
blank in every section.

Two things were wrong. A corpus that fits in one window was being compressed at
all, which spends model calls and risks the whole run to save nothing. And when
compression failed completely, the documents were discarded rather than sent.
"""

import pytest

from engine import llm
from engine.reports import digest as D


def _mentions(count, size=200):
    return [{"id": f"r{i}", "title": "T", "text": "x" * size, "platform": "nation",
             "source_type": "article", "posted_at": "2026-06-01"} for i in range(count)]


@pytest.fixture()
def no_model(monkeypatch):
    """Every model call fails, exactly as a refusing free tier behaves."""
    def boom(*a, **k):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr(llm, "call_json", boom)
    return boom


def test_a_small_corpus_is_never_compressed(no_model):
    """23 documents fit in one window, so there is nothing to compress — and
    with no compression there is nothing to fail."""
    result = D.build_corpus_digest("X", _mentions(23))
    coverage = result["coverage"]
    assert coverage["mode"] == "raw"
    assert coverage["mentions_analyzed"] == 23
    assert coverage["complete"] is True
    assert coverage["chunks"] == 0, "a model call was made that was not needed"


def test_the_analysts_get_the_documents_not_an_empty_digest(no_model):
    result = D.build_corpus_digest("X", _mentions(23))
    context = D.digest_context(result, 90000)
    assert context.strip()
    assert "r0" in context and "r22" in context
    assert context.startswith("THESE ARE THE SOURCE DOCUMENTS")


def test_a_total_compression_failure_falls_back_to_the_documents(no_model):
    """The large-corpus case: every pass fails, so send what fits rather than
    nothing."""
    result = D.build_corpus_digest("X", _mentions(400, size=6000))
    coverage = result["coverage"]
    assert coverage["mode"] == "raw_fallback"
    assert coverage["chunks_failed"] == coverage["chunks"] > 0
    # The honesty accounting is untouched: nothing was READ by the model.
    assert coverage["mentions_analyzed"] == 0
    assert coverage["complete"] is False
    assert D.digest_context(result, 90000).strip()


def test_a_partial_failure_still_uses_the_digests_it_has(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] % 2:
            raise RuntimeError("429")
        return {"digest": {"claims": [{"ref": "r1", "text": "a claim"}]}}

    monkeypatch.setattr(llm, "call_json", flaky)
    result = D.build_corpus_digest("X", _mentions(400, size=6000))
    assert result["coverage"].get("mode") not in ("raw", "raw_fallback")
    assert result["coverage"]["mentions_analyzed"] > 0
    assert "a claim" in D.digest_context(result, 90000)


def test_an_empty_corpus_is_still_an_empty_corpus(no_model):
    result = D.build_corpus_digest("X", [])
    assert result["coverage"]["mentions_total"] == 0
    assert D.digest_context(result, 1000) in ("", "[]")


def test_the_window_is_respected():
    huge = _mentions(500, size=6000)
    assert not D.fits_without_compression(huge, 90000)
    assert len(D.raw_corpus_context(huge, 90000)) <= 90000


def test_a_map_with_no_working_model_now_reads_its_documents(no_model):
    from engine.reports import issue_map

    corpus = [{"id": "a", "title": "Okiya Omtatah sues National Treasury over IMF loans",
               "text": "The National Treasury was named.", "platform": "nation",
               "source_type": "article", "posted_at": "2026-06-01T00:00:00",
               "source_url": "https://n/1"}] * 23
    payload = issue_map.build_issue_map("Okiya Omtatah", "IMF", mentions=corpus)
    assert payload["coverage"]["mode"] == "raw"
    assert payload["coverage"]["mentions_analyzed"] == 23, "'analysed 0 of 23' again"

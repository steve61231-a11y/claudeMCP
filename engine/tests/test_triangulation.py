"""Triangulation: how many INDEPENDENT sources actually back a claim, not
how many times it's repeated. 'Let's have triangulation on the major bold
facts, so we know the AI is not hallucinating.'
"""

from engine.reports import triangulation


def test_two_distinct_outlets_confirm_a_claim():
    rows = [{"platform": "standardmedia.co.ke", "url": "https://a"},
            {"platform": "the-star.co.ke", "url": "https://b"}]
    result = triangulation.corroboration(rows)
    assert result["confirmed"] is True
    assert result["independent_sources"] == 2
    assert "Confirmed" in result["label"]


def test_one_outlet_is_reported_not_confirmed():
    rows = [{"platform": "youtube", "url": "https://a"}]
    result = triangulation.corroboration(rows)
    assert result["confirmed"] is False
    assert "single source" in result["label"]


def test_the_same_outlet_repeated_still_counts_as_one_source():
    """Three reposts of one wire story are one source, not three."""
    rows = [{"platform": "youtube", "url": "https://a"},
            {"platform": "youtube", "url": "https://b"},
            {"platform": "YouTube", "url": "https://c"}]  # case-insensitive
    result = triangulation.corroboration(rows)
    assert result["independent_sources"] == 1
    assert result["confirmed"] is False


def test_an_unresolved_citation_cannot_corroborate_anything():
    rows = [{"platform": "youtube", "url": None}, {"platform": None, "url": "https://a"}]
    result = triangulation.corroboration(rows)
    assert result["independent_sources"] == 0
    assert "No independently" in result["label"]


def test_no_rows_is_the_no_source_state():
    result = triangulation.corroboration([])
    assert result["independent_sources"] == 0


def test_annotate_scores_the_verdict_from_its_own_and_involvements_citations():
    analysis = {
        "verdict_citations": [{"platform": "a", "url": "https://a"}],
        "involvement_citations": [{"platform": "b", "url": "https://b"}],
    }
    out = triangulation.annotate(analysis)
    assert out["verdict_corroboration"]["independent_sources"] == 2
    assert out["verdict_corroboration"]["confirmed"] is True


def test_annotate_scores_every_timeline_event_independently():
    analysis = {"timeline": [
        {"date": "2026-01-01", "event": "a", "quotes": [{"platform": "x", "url": "https://x"}]},
        {"date": "2026-02-01", "event": "b", "quotes": [
            {"platform": "x", "url": "https://x"}, {"platform": "y", "url": "https://y"}]},
    ]}
    out = triangulation.annotate(analysis)
    assert out["timeline"][0]["corroboration"]["confirmed"] is False
    assert out["timeline"][1]["corroboration"]["confirmed"] is True


def test_annotate_never_mutates_the_input():
    analysis = {"verdict_citations": [{"platform": "a", "url": "https://a"}]}
    original = {"verdict_citations": [{"platform": "a", "url": "https://a"}]}
    triangulation.annotate(analysis)
    assert analysis == original


def test_annotate_handles_no_citations_and_no_timeline():
    assert triangulation.annotate({}) == {}
    assert triangulation.annotate(None) is None
    out = triangulation.annotate({"verdict": "x"})
    assert "verdict_corroboration" not in out

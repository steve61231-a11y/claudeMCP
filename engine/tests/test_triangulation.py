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


# --- the shape analysts actually produce -------------------------------------
#
# Everything above this line feeds quotes shaped {"platform":..., "url":...}.
# No analyst has ever produced that. A quote is {"ref": "abcd1234", "text":
# "..."} — the ref is how it is validated back against its source — so
# `_platforms` found nothing in it, every live timeline event was scored from
# zero outlets, and a moment reported by four newspapers was labelled "No
# independently-traceable source". The feature had never once run on real
# data, and the tests agreed with it because they described a shape that does
# not exist.

_INDEX = {
    "abcd1234": {"platform": "nation.africa", "url": "https://n/1"},
    "efgh5678": {"platform": "standardmedia.co.ke", "url": "https://s/1"},
    "ijkl9012": {"platform": "nation.africa", "url": "https://n/2"},
}


def _annotated(quotes, **extra):
    out = triangulation.annotate(
        {"ref_index": _INDEX, "timeline": [dict(extra, date="2026-06-02",
                                                event="e", quotes=quotes)]})
    return out["timeline"][0]["corroboration"]


def test_a_quote_carrying_only_a_ref_still_names_its_outlet():
    got = _annotated([{"ref": "abcd1234", "text": "q"},
                      {"ref": "efgh5678", "text": "q"}])
    assert got["independent_sources"] == 2
    assert got["confirmed"] is True
    assert got["outlets"] == ["nation.africa", "standardmedia.co.ke"]


def test_two_refs_from_one_outlet_are_one_source():
    """The whole point of counting outlets rather than mentions: a story
    syndicated twice is one source, and a graph that coloured it 'confirmed'
    would be asserting corroboration that does not exist."""
    got = _annotated([{"ref": "abcd1234", "text": "q"},
                      {"ref": "ijkl9012", "text": "q"}])
    assert got["independent_sources"] == 1
    assert got["confirmed"] is False


def test_a_ref_the_corpus_cannot_resolve_corroborates_nothing():
    """An unresolvable citation cannot be checked for independence, so it
    cannot count toward anything — and must not be silently treated as one."""
    got = _annotated([{"ref": "nowhere0", "text": "q"}])
    assert got["independent_sources"] == 0
    assert "No independently-traceable source" in got["label"]


def test_already_resolved_quotes_still_work():
    """Whatever shape a caller has, this must read it — the old shape
    included, since the issue map's own sample rows carry platform and url."""
    got = _annotated([{"platform": "citizen.digital", "url": "https://c/1"},
                      {"ref": "abcd1234", "text": "q"}])
    assert got["independent_sources"] == 2


def test_the_events_own_citations_count_too():
    """`event` is an 80-200 word briefing whose inline refs linkify resolves
    into `event_citations`. Those are sources for the event as surely as its
    quotes are."""
    got = _annotated([], event_citations=[
        {"n": 1, "ref": "abcd1234", "platform": "nation.africa", "url": "https://n/1"},
        {"n": 2, "ref": "efgh5678", "platform": "standardmedia.co.ke", "url": "https://s/1"}])
    assert got["independent_sources"] == 2


def test_the_search_report_is_triangulated_too():
    """This ran on the issue map and not on the report — the same "fixed
    where it was seen" mistake that took three rounds on citations. Both
    build timelines from the same analyst under the same rules."""
    import inspect

    from engine.reports import sections
    source = inspect.getsource(sections)
    assert "triangulation.annotate" in source, (
        "the Search report's timeline carries no corroboration")
    assert source.index("linkify_report") < source.index("triangulation.annotate"), (
        "annotate must run after linkify, which is what builds the ref index "
        "its quotes are resolved through")

"""Inline [ref=xxxx] must never reach the page verbatim.

Verdict/involvement/tension_or_risk are free-prose fields with no separate
quotes array, so the model cites the only way it can — imitating the digest's
own [ref=abcd1234 | platform ...] tagging. Left alone, that string reached
the page exactly as the model wrote it: "[ref=fresh-0]" sitting inside a
sentence a CEO is meant to read.
"""

from engine.reports import citations

MENTIONS = [
    {"id": "fresh-0", "source_url": "https://n/0", "platform": "standardmedia.co.ke"},
    {"id": "fresh-1", "source_url": "https://n/1", "platform": "youtube"},
]


def test_a_ref_becomes_a_small_sequential_number():
    text, cites = citations.linkify("IMF granted immunity [ref=fresh-0].", {})
    assert text == "IMF granted immunity [1]."
    assert cites == [{"n": 1, "ref": "fresh-0", "url": None, "platform": None}]


def test_the_same_ref_repeated_gets_the_same_number():
    text, cites = citations.linkify(
        "First claim [ref=fresh-0]. Same source again [ref=fresh-0].", {})
    assert text == "First claim [1]. Same source again [1]."
    assert len(cites) == 1


def test_two_different_refs_get_sequential_numbers_in_order_of_appearance():
    text, cites = citations.linkify(
        "A [ref=fresh-1] then B [ref=fresh-0] then A again [ref=fresh-1].", {})
    assert text == "A [1] then B [2] then A again [1]."
    assert [c["n"] for c in cites] == [1, 2]
    assert cites[0]["ref"] == "fresh-1" and cites[1]["ref"] == "fresh-0"


def test_a_resolved_ref_carries_its_real_url():
    index = citations.build_ref_index(MENTIONS)
    text, cites = citations.linkify("Struck out [ref=fresh-0].", index)
    assert cites[0]["url"] == "https://n/0"
    assert cites[0]["platform"] == "standardmedia.co.ke"


def test_an_unresolvable_ref_is_still_numbered_not_dropped():
    """Never silently discard a citation — an unresolved source is a real,
    disclosable state, not the same as no citation at all."""
    text, cites = citations.linkify("A claim [ref=nowhere00].", {})
    assert text == "A claim [1]."
    assert cites[0]["url"] is None


def test_text_with_no_refs_is_untouched():
    text, cites = citations.linkify("Plain sentence, nothing to cite.", {})
    assert text == "Plain sentence, nothing to cite."
    assert cites == []


def test_linkify_analysis_covers_every_prose_field():
    analysis = {
        "involvement": "He filed suit [ref=fresh-0].",
        "tension_or_risk": "Contested by [ref=fresh-1].",
        "verdict": "Struck out [ref=fresh-0].",
        "linking_narratives": [{"narrative": "n", "summary": "s [ref=fresh-1]"}],
        "sub_issues": [{"sub_issue": "x", "detail": "d [ref=fresh-0]"}],
    }
    out = citations.linkify_analysis(analysis, MENTIONS)

    assert "[ref=" not in out["involvement"]
    assert out["involvement_citations"][0]["url"] == "https://n/0"
    assert "[ref=" not in out["tension_or_risk"]
    assert "[ref=" not in out["verdict"]
    assert "[ref=" not in out["linking_narratives"][0]["summary"]
    assert out["linking_narratives"][0]["summary_citations"][0]["url"] == "https://n/1"
    assert "[ref=" not in out["sub_issues"][0]["detail"]


def test_linkify_analysis_never_mutates_the_input():
    analysis = {"verdict": "Claim [ref=fresh-0]."}
    original = dict(analysis)
    citations.linkify_analysis(analysis, MENTIONS)
    assert analysis == original


def test_a_field_with_nothing_to_link_gets_no_citations_key():
    analysis = {"verdict": "No citation needed here."}
    out = citations.linkify_analysis(analysis, MENTIONS)
    assert "verdict_citations" not in out


def test_empty_analysis_does_not_raise():
    assert citations.linkify_analysis({}, MENTIONS) == {}
    assert citations.linkify_analysis(None, MENTIONS) is None


# --- every prose field, not just the three that were remembered --------------

def test_the_background_analysts_prose_is_linkified_too():
    """A live map rendered a clean "[1]" verdict at the top and, four sections
    down, "operates globally alongside the World Bank [ref=2ddf5220]" — the
    model's internal citation format in the body of a client deliverable.
    `international` and `national` were never added to the field list when the
    background analyst was added.
    """
    mentions = [{"id": "2ddf5220aaaa", "platform": "worldbank.org",
                 "raw_payload": {"url": "https://wb/1"}}]
    out = citations.linkify_analysis(
        {"international": "It operates alongside the World Bank [ref=2ddf5220].",
         "national": "Kenya's debt worries the fund [ref=2ddf5220]."},
        mentions)
    assert "[ref=" not in out["international"]
    assert "[ref=" not in out["national"]
    assert out["international_citations"][0]["url"] == "https://wb/1"
    assert out["national_citations"][0]["url"] == "https://wb/1"


def test_timeline_mini_briefings_are_linkified():
    """`event` is an 80-200 word briefing, not a label — and the sequencing
    section reprints it, so one raw ref surfaced three times in one report."""
    mentions = [{"id": "fresh-0abc", "platform": "standardmedia.co.ke",
                 "raw_payload": {"url": "https://sm/1"}}]
    out = citations.linkify_analysis(
        {"timeline": [{"date": "2026-06-25",
                       "event": "The IMF was struck out [ref=fresh-0a]."}]},
        mentions)
    event = out["timeline"][0]
    assert "[ref=" not in event["event"]
    assert event["event_citations"][0]["url"] == "https://sm/1"


def test_no_prose_field_is_left_behind():
    """The list was written once and never revisited as analysts were added.
    Feed a ref into every free-text field the issue-map analysts produce and
    assert none of them reaches the page raw."""
    mentions = [{"id": "abcd1234", "platform": "nation.africa",
                 "raw_payload": {"url": "https://n/1"}}]
    raw = "text [ref=abcd1234]."
    out = citations.linkify_analysis(
        {"involvement": raw, "verdict": raw, "tension_or_risk": raw,
         "international": raw, "national": raw,
         "timeline": [{"event": raw}],
         "sub_issues": [{"detail": raw}],
         "linking_narratives": [{"summary": raw, "detail": raw}]},
        mentions)
    import json as _json
    assert "[ref=" not in _json.dumps(out), "a prose field still leaks the raw format"

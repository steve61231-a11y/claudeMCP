"""Inline [ref=xxxx] must never reach the page verbatim.

Verdict/involvement/tension_or_risk are free-prose fields with no separate
quotes array, so the model cites the only way it can — imitating the digest's
own [ref=abcd1234 | platform ...] tagging. Left alone, that string reached
the page exactly as the model wrote it: "[ref=fresh-0]" sitting inside a
sentence a CEO is meant to read.
"""

import pytest

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


# --- the Search report leaks the same way the issue map did -------------------

def test_the_search_reports_prose_is_linkified():
    """This was fixed for the issue map and not the report, because the leak
    had only been SEEN on a map. Both run the same analysts under the same
    GROUNDING_RULES, which tell the model to "include that item's ref id", and
    the report's prose fields have no quotes array to carry them either."""
    mentions = [{"id": "fresh-12ab", "platform": "nation.africa",
                 "raw_payload": {"url": "https://n/12"}}]
    out = citations.linkify_report(
        {"executive_brief": "Opposition is narrow [ref=fresh-12].",
         "executive_summary": "Net-negative sentiment [ref=fresh-12].",
         "deep_insights": {
             "the_one_thing": "A handful of accounts drive it [ref=fresh-12].",
             "insights": [{"headline": "Narrow, not wide [ref=fresh-12].",
                           "reasoning": "Volume concentrates [ref=fresh-12].",
                           "implication": "Engage the few [ref=fresh-12]."}]},
         "narrative_deep_dives": [{"deep_dive": "The SHA story [ref=fresh-12]."}]},
        mentions)

    import json as _json
    assert "[ref=" not in _json.dumps(out), "the report still leaks the raw format"
    assert out["executive_brief_citations"][0]["url"] == "https://n/12"
    assert out["deep_insights"]["the_one_thing_citations"][0]["url"] == "https://n/12"
    assert out["deep_insights"]["insights"][0]["headline_citations"]
    assert out["narrative_deep_dives"][0]["deep_dive_citations"]


def test_linkify_report_does_not_mutate_its_input():
    payload = {"executive_brief": "text [ref=abcd1234]."}
    before = dict(payload)
    citations.linkify_report(payload, [{"id": "abcd1234", "platform": "p",
                                        "raw_payload": {"url": "https://x/1"}}])
    assert payload == before


def test_a_report_with_no_refs_is_returned_unharmed():
    payload = {"executive_brief": "Plain prose, no citations.",
               "deep_insights": {"insights": [], "the_one_thing": ""}}
    out = citations.linkify_report(payload, [])
    assert out["executive_brief"] == payload["executive_brief"]
    assert "executive_brief_citations" not in out


# --- the model does not commit to one spelling -------------------------------

@pytest.mark.parametrize("raw", [
    "fact [ref=fresh-0] here.",       # square brackets — the shape first fixed
    "fact (ref=fresh-0) here.",       # round brackets — a live map used these
    "fact [ref: fresh-0] here.",      # colon
    "fact (ref = fresh-0) here.",     # spaces around the operator
    "fact ref=fresh-0 here.",         # no brackets at all
    "fact [REF=fresh-0] here.",       # shouted
])
def test_every_spelling_of_a_citation_is_resolved(raw):
    """The pattern pinned ONE exact shape, so each time a model chose another
    the raw text went to the page and the fix taught it one more spelling.
    Three rounds of that is enough: match the family."""
    out, cites = citations.linkify(raw, {"fresh-0": {"url": "https://n/0"}})
    assert "ref" not in out.lower(), f"{raw!r} still leaks: {out!r}"
    assert cites and cites[0]["url"] == "https://n/0"


def test_several_refs_in_one_marker_each_get_a_link():
    """A live map wrote "(ref=fresh-0, fresh-1)" — two sources in one marker.
    Numbering the group once would credit both facts to one article."""
    out, cites = citations.linkify(
        "the core, verifiable fact (ref=fresh-0, fresh-1).",
        {"fresh-0": {"url": "https://n/0"}, "fresh-1": {"url": "https://n/1"}})
    assert out == "the core, verifiable fact [1][2]."
    assert [c["url"] for c in cites] == ["https://n/0", "https://n/1"]


def test_the_words_around_a_bare_ref_keep_their_spaces():
    out, _ = citations.linkify("bare ref=fresh-0 here.", {"fresh-0": {"url": "u"}})
    assert out == "bare [1] here."


def test_ordinary_prose_is_never_mistaken_for_a_citation():
    """Widening the brackets must not start eating real sentences. The `ref=`
    anchor is what keeps this safe."""
    for prose in ("a referendum on the referral of the matter",
                  "references were checked",
                  "the ref blew the whistle"):
        out, cites = citations.linkify(prose, {})
        assert out == prose and cites == []


def test_the_exact_paragraph_from_the_live_report():
    """Verbatim from a run that shipped this to the page."""
    raw = ("That is the core, verifiable fact (ref=fresh-0, fresh-1). "
           "The immunity challenge (ref=fresh-3) is core but does not show "
           "the outcome.")
    out, cites = citations.linkify(
        raw, {"fresh-0": {"url": "https://n/0"}, "fresh-1": {"url": "https://n/1"},
              "fresh-3": {"url": "https://n/3"}})
    assert "ref=" not in out
    assert len(cites) == 3


def test_no_report_prose_field_is_left_behind():
    """The mirror of test_no_prose_field_is_left_behind, for the search report.

    That test existed for the issue map and this one did not, so `linkify_report`
    covered the four fields whose leak had been SEEN — brief, summary, insights,
    deep-dives — and left the rest. A rendered PDF then carried seven raw refs
    in the timeline, the narratives and the storyline descriptions while those
    four were clean.
    """
    import json as _json

    mentions = [{"id": "abcd1234", "platform": "nation.africa",
                 "raw_payload": {"url": "https://n/1"}}]
    raw = "text [ref=abcd1234] and (ref=abcd1234)."
    out = citations.linkify_report({
        "executive_brief": raw,
        "executive_summary": raw,
        "deep_insights": {"the_one_thing": raw,
                          "insights": [{"headline": raw, "reasoning": raw, "implication": raw}]},
        "narrative_deep_dives": [{"deep_dive": raw}],
        "timeline": [{"date": "2026-09-09", "event": raw}],
        "narrative_breakdown": [{"label": "N", "description": raw, "summary": raw}],
        "public_voice": {"supportive": [{"theme": "T", "summary": raw}],
                         "critical": [{"theme": "T", "summary": raw}],
                         "neutral": [{"theme": "T", "summary": raw}]},
        "influencer_stances": [{"handle": "@x", "summary": raw}],
        "risks": [{"risk": "R", "detail": raw}],
        "opportunities": [{"opportunity": "O", "detail": raw}],
        "trends": [{"trend": "T", "detail": raw}],
    }, mentions)
    assert "ref=" not in _json.dumps(out), "a report prose field still leaks the raw format"


def test_the_report_timeline_briefing_is_linkified():
    """`event` is an 80-200 word briefing, the longest prose in the report."""
    out = citations.linkify_report(
        {"timeline": [{"event": "Launches the platform (ref=abcd1234)."}]},
        [{"id": "abcd1234", "platform": "p", "raw_payload": {"url": "https://n/1"}}])
    assert "ref=" not in out["timeline"][0]["event"]
    assert out["timeline"][0]["event_citations"][0]["url"] == "https://n/1"


# --- coverage is a rule, not a list ------------------------------------------
#
# Everything below exists because `test_no_report_prose_field_is_left_behind`,
# directly above, is named like a rule and written like an instance: it seeds a
# ref into the twelve fields whose names someone remembered, so it passed for
# months while six other fields shipped raw `[ref=...]` to the page. A test
# that enumerates the same list as the code can only ever confirm the code
# matches itself.

_M = [{"id": "abcd1234", "platform": "nation.africa",
       "raw_payload": {"url": "https://n/1"}}]
_RAW = "a sentence [ref=abcd1234]."


def test_a_prose_field_nobody_has_thought_of_yet_is_still_linkified():
    """The point of the traversal. These key names are invented — no list in
    the codebase contains them, and no list ever could, because the next one
    will be invented by whoever writes the next prompt."""
    import json as _json
    out = citations.linkify_report({
        "some_future_analyst": {
            "a_field_added_next_month": _RAW,
            "nested": [{"deeper": [{"deepest": _RAW}]}],
        }}, _M)
    assert "ref=" not in _json.dumps(out)


@pytest.mark.parametrize("payload,path", [
    ({"influencer_stances": [{"handle": "@x", "what_they_say": _RAW}]},
     "what_they_say"),
    ({"platform_pulse": [{"platform": "tiktok", "tone": _RAW}]}, "tone"),
    ({"narrative_deep_dives": [{"deep_dive": {"how_it_unfolded": _RAW}}]},
     "how_it_unfolded"),
    ({"narrative_deep_dives": [{"deep_dive": {"supporter_framing": _RAW}}]},
     "supporter_framing"),
    ({"narrative_deep_dives": [{"deep_dive": {"critic_framing": _RAW}}]},
     "critic_framing"),
])
def test_the_six_fields_the_list_never_covered(payload, path):
    """Every one of these leaked in a shipped 66-page report while the twelve
    listed fields were clean.

    Two of them are worth naming. `how_it_unfolded` is the 250-500 word
    deep-dive body — the longest prose block the report produces. And
    `influencer_stances` WAS on the list, under the key `summary`, which the
    analyst has never emitted: the field is `what_they_say`, so that entry had
    been "fixed" and had never once run.
    """
    import json as _json
    out = citations.linkify_report(payload, _M)
    assert "ref=" not in _json.dumps(out), f"{path} still leaks"


def test_the_issue_map_and_the_report_cannot_drift_apart():
    """They run the same analysts under the same GROUNDING_RULES, and the
    linkifier fell behind on one and not the other three separate times. They
    are now one function, so the only way to fix one is to fix both."""
    payload = {"anything": _RAW}
    assert (citations.linkify_report(payload, _M)
            == citations.linkify_analysis(payload, _M))


# --- what the traversal must NOT touch ---------------------------------------

def test_a_url_containing_ref_is_not_eaten():
    """Walking every string put addresses in reach of the pattern, and
    `?ref=` is an ordinary tracking parameter. Rewriting it to "[1]" turns a
    working citation into a dead link — the traversal causing the exact class
    of damage it exists to prevent."""
    out, cites = citations.linkify(
        "see https://example.com/x?ref=fb for more", {})
    assert out == "see https://example.com/x?ref=fb for more"
    assert cites == []


def test_a_real_citation_beside_a_url_still_resolves():
    """Skipping URLs must not skip the sentence containing one."""
    out, cites = citations.linkify(
        "the filing at https://kenyalaw.org/view?ref=12345 [ref=abcd1234].",
        {"abcd1234": {"url": "https://n/1"}})
    assert out == "the filing at https://kenyalaw.org/view?ref=12345 [1]."
    assert len(cites) == 1


def test_a_bare_url_field_is_left_alone():
    out = citations.linkify_report(
        {"sources": [{"url": "https://example.com/a?ref=twitter"}]}, _M)
    assert out["sources"][0]["url"] == "https://example.com/a?ref=twitter"


def test_a_quotes_structured_ref_and_verbatim_text_survive():
    """`quotes[].ref` is an id the page resolves itself, and `text` is checked
    against the source by `_validate_quotes`. Numbering either would break
    both."""
    out = citations.linkify_report(
        {"public_voice": {"supportive": [
            {"theme": "T", "quotes": [{"ref": "abcd1234", "text": "verbatim"}]}]}},
        _M)
    quote = out["public_voice"]["supportive"][0]["quotes"][0]
    assert quote == {"ref": "abcd1234", "text": "verbatim"}


def test_linkifying_twice_is_a_no_op():
    """The pipeline publishes incrementally and a payload can be passed
    through more than once. A second pass must not renumber the citations it
    produced the first time."""
    once = citations.linkify_report({"executive_brief": _RAW}, _M)
    twice = citations.linkify_report(once, _M)
    assert twice["executive_brief"] == once["executive_brief"]
    assert twice["executive_brief_citations"] == once["executive_brief_citations"]


# --- numbering and provenance ------------------------------------------------

def test_a_list_of_strings_shares_one_numbering():
    """`themes` and `who_is_driving_it` are lists of short prose sharing a
    single citations array. Numbering each entry from [1] would show a reader
    two different sources both labelled [1]."""
    out = citations.linkify_report(
        {"platform_pulse": [{"themes": ["first [ref=abcd1234].",
                                        "second [ref=efgh5678]."]}]},
        _M + [{"id": "efgh5678", "platform": "x", "raw_payload": {"url": "https://n/2"}}])
    pulse = out["platform_pulse"][0]
    assert pulse["themes"] == ["first [1].", "second [2]."]
    assert [c["n"] for c in pulse["themes_citations"]] == [1, 2]


def test_the_page_is_given_what_it_needs_to_name_a_source():
    """The page rendered `ref 37d358e4` under every quote — an internal id
    where the outlet belongs. It had no way to do better: the resolved sources
    existed only inside this module. Now they ship."""
    out = citations.linkify_report(
        {"public_voice": {"supportive": [
            {"quotes": [{"ref": "abcd1234", "text": "q"}]}]}},
        _M)
    assert out["ref_index"]["abcd1234"]["platform"] == "nation.africa"
    assert out["ref_index"]["abcd1234"]["url"] == "https://n/1"


def test_the_ref_index_carries_only_what_is_cited():
    """A run has hundreds of mentions and dozens of quotes. Shipping the whole
    corpus index to the browser would be most of a megabyte of rows nothing
    refers to."""
    corpus = _M + [{"id": f"unused{i:03d}", "platform": "x",
                    "raw_payload": {"url": f"https://n/{i}"}} for i in range(200)]
    out = citations.linkify_report({"executive_brief": _RAW}, corpus)
    assert list(out["ref_index"]) == ["abcd1234"]


def test_a_report_with_nothing_cited_ships_no_index():
    out = citations.linkify_report({"executive_brief": "No citations here."}, _M)
    assert "ref_index" not in out


def test_everything_the_linkifier_adds_survives_the_database():
    """The payload is stored in a JSONB column and served as JSON. `ref_index`
    was added carrying `posted_at` straight from the ORM — a live `datetime`,
    which the index had never needed to be free of because it had never left
    this module. Every end-to-end test failed on the insert.
    """
    import datetime as _dt
    import json as _json

    out = citations.linkify_report(
        {"executive_brief": _RAW},
        [{"id": "abcd1234", "platform": "nation.africa",
          "posted_at": _dt.datetime(2026, 3, 12, 9, 0),
          "raw_payload": {"url": "https://n/1"}}])
    _json.dumps(out)     # the assertion: this is the insert that failed
    assert out["ref_index"]["abcd1234"]["posted_at"].startswith("2026-03-12")

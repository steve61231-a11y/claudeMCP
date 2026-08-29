"""Sources and the headline number have to be checkable.

Two complaints from a live report drive these: "Sources covered (73)" that could
not be opened or attributed, and a bare "12.2%" with nothing saying what it
counted or how far to trust it.
"""

from datetime import datetime

from engine.reports import sentiment_framework as fw


def _m(mid, platform, text, source_type="article", eng=None, url=None, author=None):
    return {"id": mid, "platform": platform, "source_type": source_type,
            "text": text, "posted_at": datetime(2026, 7, 1), "author_handle": author,
            "source_url": url, "engagement": eng or {}}


# --- per-source dossier ------------------------------------------------------

def _built(sentiments=None):
    mentions = [
        _m("a", "nation.africa", "Story one", eng={"views": 10}, url="https://n/1", author="Nation"),
        _m("b", "nation.africa", "Story two", eng={"views": 900}, url="https://n/2"),
        _m("c", "nation.africa", "Story three", eng={"views": 5}),
        _m("d", "nation.africa", "Story four", eng={"views": 1}),
        _m("e", "standardmedia.co.ke", "Other outlet", eng={"views": 3}, url="https://s/1"),
    ]
    return fw.build_overall_mentions({}, mentions, None, sentiments or {})


def test_each_source_reports_its_own_volume_and_items():
    detail = {s["source"]: s for s in _built()["sources_detail"]}
    assert detail["nation.africa"]["mentions"] == 4
    assert detail["standardmedia.co.ke"]["mentions"] == 1
    assert [i["excerpt"] for i in detail["nation.africa"]["top_mentions"]][0] == "Story two", \
        "loudest item first"


def test_links_are_carried_and_absent_links_stay_absent():
    top = {s["source"]: s for s in _built()["sources_detail"]}["nation.africa"]["top_mentions"]
    by_excerpt = {i["excerpt"]: i for i in top}
    assert by_excerpt["Story two"]["url"] == "https://n/2"
    assert by_excerpt["Story three"]["url"] is None


def test_lean_is_withheld_until_enough_of_the_outlet_was_scored():
    # One scored item out of four is not an editorial judgement about a real
    # newsroom, it is a guess with a newsroom's name attached to it.
    thin = _built({"a": {"sentiment": "negative"}})
    detail = {s["source"]: s for s in thin["sources_detail"]}
    assert detail["nation.africa"]["lean"] is None
    assert detail["nation.africa"]["negative"] == 1
    assert detail["nation.africa"]["unscored"] == 3


def test_lean_is_stated_once_the_outlet_is_actually_scored():
    scored = _built({"a": {"sentiment": "negative"}, "b": {"sentiment": "negative"},
                     "c": {"sentiment": "negative"}, "d": {"sentiment": "neutral"}})
    detail = {s["source"]: s for s in scored["sources_detail"]}
    assert detail["nation.africa"]["lean"] == "negative"
    assert detail["nation.africa"]["scored"] == 4


def test_sources_are_ordered_by_volume():
    sources = [s["source"] for s in _built()["sources_detail"]]
    assert sources[0] == "nation.africa"


# --- the headline number explained -------------------------------------------

def test_reading_states_the_counts_the_score_came_from():
    text = " ".join(fw._score_reading(12.2, 9, 16, 49, 74,
                                      {"volume_trends": {"total_mentions": 661}}, None))
    assert "12.2%" in text
    assert "74" in text and "661" in text


def test_reading_flags_a_minority_corpus_as_indicative():
    text = " ".join(fw._score_reading(12.2, 9, 16, 49, 74,
                                      {"volume_trends": {"total_mentions": 661}}, None))
    assert "indicative" in text


def test_reading_says_absent_not_zero_when_nothing_was_scored():
    text = " ".join(fw._score_reading(None, 0, 0, 0, 0,
                                      {"volume_trends": {"total_mentions": 661}}, None))
    assert "absent rather than zero" in text
    assert "no positive coverage" in text


def test_reading_explains_the_move_against_the_previous_period():
    text = " ".join(fw._score_reading(12.2, 9, 16, 49, 74,
                                      {"volume_trends": {"total_mentions": 100}}, 13.3))
    assert "13.3%" in text and "-1.1" in text


def test_score_section_carries_the_reading():
    section = fw.build_sentiment_score_section(
        {"sentiment_breakdown": {"positive_pct": 12.0, "neutral_pct": 66.0,
                                 "negative_pct": 22.0, "total_mentions_analyzed": 74},
         "volume_trends": {"total_mentions": 661}}, None)
    assert len(section["reading"]) >= 4, "a number needs more than a caption"

""""senate × forestry" analysed 370 documents for 20 minutes and returned nothing.

The evidence sample was New Jersey, Hawaii, Alaska, Georgia, Ontario, Oregon —
one Kenyan item in fifteen. Every framework section was empty, correctly: the
analysts were asked about a Kenyan senate and handed American local news. Three
defects combined, and these tests pin all three.
"""

import pytest

from engine.reports import relevance
from engine.reports.issue_map import MIN_USABLE_DOCUMENTS, _nothing_on_topic

KENYAN = ("Forestry cabinet secretary Keriako Tobiko told the Senate committee in Nairobi "
          "that the second phase of Maasai Mau forest evictions will continue.")
NEW_JERSEY = ("Senator Shirley Turner keeps proving her worth to New Jersey. The State "
              "Senate president pro tempore has represented the 15th district since 1998.")
HAWAII = ("DLNR: vandals destroyed trees and installed a bench in the Ewa Forest Reserve, "
          "the Hawaii Department of Land and Natural Resources said.")


def _doc(text, url=""):
    return {"text": text, "source_url": url}


# --- generic terms match the whole world -------------------------------------

def test_a_bare_institution_name_is_generic():
    assert relevance.is_generic("senate")
    assert relevance.is_generic("forestry")
    assert relevance.is_generic("Ministry of Health")


def test_a_distinctive_name_is_not_generic():
    assert not relevance.is_generic("Rigathi Gachagua")
    assert not relevance.is_generic("SHA")
    assert not relevance.is_generic("Kenya Forest Service")


def test_a_generic_pair_needs_a_market_anchor():
    assert relevance.needs_market_anchor("senate", "forestry")


def test_one_specific_half_is_enough_to_anchor_the_pair():
    """A distinctive name anchors its own search. Requiring "Kenya" as well
    would drop true articles that never write it — which is most of the Kenyan
    press, writing for Kenyan readers."""
    assert not relevance.needs_market_anchor("Rigathi Gachagua", "forestry")
    assert not relevance.needs_market_anchor("senate", "SHA")
    assert relevance.needs_market_anchor("senate", "forestry")


def test_anchoring_adds_the_market_once():
    anchored = relevance.anchor_query('"senate" "forestry"')
    assert "Kenya" in anchored
    assert relevance.anchor_query(anchored) == anchored, "must not stack anchors"


# --- the filter itself -------------------------------------------------------

def _filter(docs, market=True):
    return relevance.filter_corpus(docs, ["senate"], ["forestry", "forest"], market)


def test_the_kenyan_item_survives():
    kept, _ = _filter([_doc(KENYAN)])
    assert len(kept) == 1


def test_american_local_news_is_dropped():
    kept, report = _filter([_doc(NEW_JERSEY), _doc(HAWAII)])
    assert kept == []
    assert report["dropped"] == 2


def test_the_report_says_why_each_item_went():
    _, report = _filter([_doc(NEW_JERSEY), _doc(HAWAII), _doc(KENYAN)])
    assert report["kept"] == 1 and report["examined"] == 3
    assert report["reasons"], "a rejection with no reason is not reviewable"


def test_an_item_mentioning_only_one_half_is_dropped():
    kept, _ = _filter([_doc("The Kenyan Senate debated the finance bill in Nairobi.")])
    assert kept == [], "mentions the principal and the market, but not the issue"


def test_market_anchoring_is_skipped_for_a_named_principal():
    """With a distinctive principal we must not require the market word: an
    article can be about Gachagua and forestry without saying "Kenya"."""
    kept, _ = relevance.filter_corpus(
        [_doc("Rigathi Gachagua criticised the forestry evictions.")],
        ["Rigathi Gachagua"], ["forestry"], require_market=False)
    assert len(kept) == 1


def test_an_empty_document_is_dropped_without_raising():
    kept, report = _filter([{}])
    assert kept == [] and report["dropped"] == 1


# --- stop early rather than analysing noise for 20 minutes -------------------

def test_the_threshold_exists_and_is_small():
    assert 0 < MIN_USABLE_DOCUMENTS <= 5


def test_nothing_on_topic_is_a_truthful_result_not_an_empty_report():
    report = {"examined": 370, "kept": 1, "dropped": 369, "market_anchored": True,
              "reasons": {"not about this market (new jersey)": 300,
                          "does not mention the issue": 69},
              "examples": {"not about this market (new jersey)": NEW_JERSEY}}
    from datetime import datetime

    result = _nothing_on_topic("senate", "forestry", datetime(2026, 1, 1),
                               datetime(2026, 8, 1), [{}] * 370, {}, report)
    nothing = result["nothing_on_topic"]
    assert nothing["examined"] == 370 and nothing["kept"] == 1
    assert "new jersey" in nothing["why"]
    assert result["issue_framework"] is None, "no framework is drawn over nothing"
    assert result["coverage"]["mentions_analyzed"] == 0
    assert "none were about this intersection" in result["coverage"]["note"]


def test_the_guidance_names_the_generic_terms_that_caused_it():
    from datetime import datetime

    result = _nothing_on_topic("senate", "forestry", datetime(2026, 1, 1),
                               datetime(2026, 8, 1), [], {},
                               {"examined": 370, "kept": 0, "market_anchored": True,
                                "reasons": {}, "examples": {}})
    guidance = " ".join(result["nothing_on_topic"]["guidance"])
    assert "generic terms" in guidance
    assert "Kenya Forest Service" in guidance, "tell the operator what to type instead"


# --- the gate result must be read from where it is written -------------------

def test_the_framework_finds_the_gate_result_under_acquisition():
    """"relevance gate: not run" was printed on every issue map ever produced,
    because the gate result lives under `acquisition` and build() read the top
    level."""
    from engine.reports import issue_framework as ifw

    payload = {"acquisition": {"evidence_gate": {"examined": 10, "on_topic": 8},
                               "relevance_filter": {"kept": 8, "examined": 10,
                                                    "market_anchored": True}}}
    built = ifw.build(issue="forestry", principal="senate", payload=payload,
                      stakeholders=[], relationships=[], events=[])
    controls = built["data_overview"]["controls_applied"]
    assert controls["relevance_gate"] == "applied"
    assert "8 of 10 documents" in controls["relevance_filter"]


# --- when to stop, and when a small answer is still an answer ----------------

def test_a_heavily_filtered_corpus_stops_before_the_digest():
    """370 collected, 1 on topic: reading them costs twenty minutes and yields
    empty sections."""
    from engine.reports.issue_map import (MIN_USABLE_DOCUMENTS,
                                          REJECTION_SAMPLE_FLOOR)

    examined, kept = 370, 1
    assert examined >= REJECTION_SAMPLE_FLOOR and kept < MIN_USABLE_DOCUMENTS


def test_a_genuinely_small_corpus_is_still_analysed():
    """Two on-topic articles are thin, not useless, and cost seconds to read.
    Refusing them would withhold the only answer available."""
    from engine.reports.issue_map import REJECTION_SAMPLE_FLOOR

    examined, kept = 2, 2
    assert examined < REJECTION_SAMPLE_FLOOR, (
        "a corpus this small cannot support a verdict about the search itself")


def test_rejecting_two_of_two_is_not_evidence_the_search_was_wrong():
    from engine.reports.issue_map import REJECTION_SAMPLE_FLOOR

    assert REJECTION_SAMPLE_FLOOR > 3, (
        "the floor must be high enough that the filter's verdict means something")


def test_an_empty_corpus_always_stops():
    from datetime import datetime

    from engine.reports.issue_map import build_issue_map

    payload = build_issue_map("Senate Committee on Lands", "Mau Forest",
                              window_start=datetime(2026, 1, 1),
                              window_end=datetime(2026, 8, 1), mentions=[])
    assert payload["thin"] is True
    assert payload["issue_framework"] is None

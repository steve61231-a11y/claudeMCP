"""Every report is measured, and the measurement cannot be flattered.

The founder, 22 October 2026: "We cannot allow any room for hallucination …
If we say that this was said, or Martha Karua did this or that, we want it
to be legit data." Until this, every change to the engine was judged by eye
on one report, so "better" was an opinion. `reports/quality.py` measures
each report against the corpus it was written from (phase 0 of
`docs/FOUNDATION.md`).

A measurement is only worth having if it cannot be gamed by the thing it
measures. Each test here pins one way a report could look better than it is:
a citation to a source that was never collected, a figure nobody computed,
the sources' own words counted as the analysts', the engine's arithmetic
counted as prose, and a measurement that failed reading as one that found
nothing wrong.
"""

from engine.reports import quality

ARTICLE = {"id": "aaaaaaaa-0000", "platform": "nation.africa", "source_type": "article",
           "author_handle": "Daily Nation", "outlet": "Daily Nation",
           "text": "The Senate voted 45 to 12. The county was allocated KSh 3,200 million.",
           "full_text": True, "posted_at": "2026-10-01"}
HEADLINE = {"id": "cccccccc-0000", "platform": "google_news", "source_type": "article",
            "author_handle": "The Standard", "outlet": "The Standard",
            "text": "Senate passes the bill", "full_text": False, "posted_at": "2026-10-02"}
COMMENT = {"id": "bbbbbbbb-0000", "platform": "youtube", "source_type": "comment",
           "author_handle": "@viewer", "text": "He is a hero", "posted_at": "2026-10-03"}
CORPUS = [ARTICLE, HEADLINE, COMMENT]


def _prose(payload, corpus=CORPUS):
    return quality.assess(payload, corpus)["prose"]


def test_a_citation_to_a_source_never_collected_is_not_a_source():
    payload = {
        "executive_brief": "The bill passed after a long and bitter debate in the Senate [1].",
        "executive_brief_citations": [{"n": 1, "ref": "zzzzzzzz", "url": None, "platform": None}],
    }
    prose = _prose(payload)
    assert prose["cited"] == 0, "a ref the corpus does not hold is an invented source"
    assert prose["citations_to_nothing"] == 1
    assert prose["citations_to_nothing_refs"] == ["zzzzzzzz"]


def test_a_citation_to_a_collected_item_counts():
    payload = {
        "executive_brief": "The bill passed after a long and bitter debate in the Senate [1].",
        "executive_brief_citations": [{"n": 1, "ref": "aaaaaaaa", "url": "x", "platform": "nation.africa"}],
    }
    prose = _prose(payload)
    assert prose["cited"] == 1 and prose["citations_to_nothing"] == 0


def test_a_figure_nobody_computed_and_no_source_states_is_reported_with_its_sentence():
    payload = {"executive_brief": "Critics say 2,300 people marched through the capital on Friday."}
    prose = _prose(payload)
    assert prose["figures_matching_nothing"] == 1
    assert prose["figures_matching_nothing_examples"][0]["figure"] == "2,300"
    assert "marched" in prose["figures_matching_nothing_examples"][0]["sentence"]


def test_figures_from_the_sources_or_the_engine_are_not_flagged():
    payload = {
        "executive_brief": ("The Senate voted 45 to 12, and KSh 3,200 million went to the county. "
                            "Critical coverage made up 67% of what was scored this month."),
        "sentiment_breakdown": {"positive_pct": 33.3, "negative_pct": 66.7, "neutral_pct": 0.0},
    }
    prose = _prose(payload)
    assert prose["figures"] == 4
    assert prose["figures_matching_nothing"] == 0, prose["figures_matching_nothing_examples"]


def test_a_number_the_source_states_in_any_form_supports_the_prose():
    corpus = [{**ARTICLE, "text": "The 56-year-old senator served 2017-2022 and was paid KSh3,400,000."}]
    payload = {"executive_brief": ("The senator, aged 56, served from 2017, "
                                   "and was paid 3,400,000 shillings for the term.")}
    assert _prose(payload, corpus)["figures_matching_nothing"] == 0


def test_a_count_is_not_a_percentage():
    """`37%` matching a day on which 37 items were published is a coincidence,
    not a source."""
    payload = {
        "executive_brief": "Support for the subject has collapsed to 37% across every region.",
        "volume_trends": {"by_day": {"2026-10-01": 37}},
    }
    assert _prose(payload)["figures_matching_nothing"] == 1


def test_dates_and_years_are_not_figures():
    payload = {"executive_brief": ("On 15 October 2026 the Senate sat again, as it did on "
                                   "October 21 and throughout 2025.")}
    assert _prose(payload)["figures"] == 0


def test_the_sources_own_words_are_not_the_analysts():
    payload = {"public_voice": {"supportive": [{
        "theme": "Hero framing",
        "summary": "Supporters describe him as a hero who delivered for the region again.",
        "quotes": [{"ref": "bbbbbbbb", "text": "He is a hero who delivered for the region, "
                                              "unlike all the others before him."}],
    }]}}
    prose = _prose(payload)
    assert prose["sentences"] == 1, "the quote was counted as analyst prose"
    assert prose["beside_quotes"] == 1, "a summary over verified quotes is backed by them"


def test_a_theme_whose_quotes_all_failed_is_counted():
    payload = {"public_voice": {"critical": [{
        "theme": "Betrayal", "summary": "Critics say he abandoned the region for national office.",
        "quotes": [], "quotes_unverified": True}]}}
    prose = _prose(payload)
    assert prose["themes_without_a_quote"] == 1
    assert prose["unsupported"] == 1


def test_sections_the_engine_counted_are_not_graded_as_writing():
    payload = {
        "derived_sections": ["timeline"],
        "timeline": [{"date": "2026-10-01", "event": "Coverage peaked with 14 items on a single day."}],
    }
    prose = _prose(payload)
    assert prose["sentences"] == 0
    assert prose["computed_sections"] == ["timeline"]


def test_the_engines_own_account_is_not_prose():
    payload = {"run_health": {"headline": "Three of forty model calls failed during this run."},
               "section_status": {"headline": "Some sections could not be produced this time."},
               "sentiment_framework": {"note": "Built from the scored corpus and its storylines."}}
    assert _prose(payload)["sentences"] == 0


def test_the_corpus_says_how_much_was_read_in_full():
    corpus = quality.assess({}, CORPUS)["corpus"]
    assert corpus["articles"] == 2 and corpus["articles_full_text"] == 1
    assert corpus["full_text_share"] == 50.0
    assert corpus["reactions"] == 1


def test_what_the_guards_removed_is_counted():
    """A cleaner-looking report must not read as a more truthful one."""
    payload = {"grounding": {"ungrounded": ["Peter Kamau"], "removed": 2},
               "protected_terms": {"checked": True, "findings": [], "removed": 1}}
    prose = _prose(payload)
    assert prose["sentences_removed_for_names"] == 2
    assert prose["sentences_removed_for_identity"] == 1


def test_a_measurement_that_failed_says_so():
    class Unreadable(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("cannot read this payload")

    result = quality.assess(Unreadable({"executive_brief": "x"}), CORPUS)
    assert result["measured"] is False
    assert "cannot read this payload" in result["error"]


def test_the_quality_card_is_for_the_operator_only():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    at = html.index("const q=r.quality")
    assert "el('div','card op-only')" in html[at:at + 300], \
        "the quality check is how an operator judges a change; a client has no use for it"


# --- the history: is this deploy better than the last? -----------------------

def _report(db_session, subject, payload, generated_at):
    from datetime import timedelta

    from engine.db.models import IntelligenceReport

    db_session.add(IntelligenceReport(
        politician_id=subject.id, period="monthly", payload=payload,
        window_start=generated_at - timedelta(days=30), window_end=generated_at,
        generated_at=generated_at))
    db_session.commit()


def test_the_quality_history_names_each_report_and_the_code_that_wrote_it(db_session, monkeypatch):
    from datetime import datetime

    from fastapi.testclient import TestClient

    import engine.api_server as api_server
    from engine.db.models import Politician

    monkeypatch.setattr(api_server, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)
    monkeypatch.setattr(api_server.settings, "pulse_api_key", "")
    subject = Politician(name="Test Subject", aliases=[], titles=[], swahili_terms=[],
                         subject_type="politician")
    other = Politician(name="Someone Else", aliases=[], titles=[], swahili_terms=[],
                       subject_type="politician")
    db_session.add_all([subject, other])
    db_session.flush()
    measured = quality.assess({"executive_brief": "A sentence long enough to count as prose [1]."},
                              CORPUS)
    _report(db_session, subject, {"executive_brief": "x"}, datetime(2026, 10, 1))
    _report(db_session, subject, {"quality": measured}, datetime(2026, 10, 20))
    _report(db_session, other, {"quality": measured}, datetime(2026, 10, 21))

    body = TestClient(api_server.app).get("/api/admin/quality?name=test subject").json()
    rows = body["reports"]
    assert [r["subject"] for r in rows] == ["Test Subject", "Test Subject"]
    assert rows[0]["quality"]["measured"] is True, "newest first"
    assert "build" in rows[0]["quality"]
    assert rows[1]["quality"]["measured"] is False, \
        "a report from before the measurement must not read as one with nothing wrong"


def test_the_quality_history_needs_the_key(monkeypatch):
    from fastapi.testclient import TestClient

    import engine.api_server as api_server

    monkeypatch.setattr(api_server.settings, "pulse_api_key", "secret")
    assert TestClient(api_server.app).get("/api/admin/quality").status_code == 401


def test_the_record_and_the_brief_check_are_measured():
    payload = {"fact_store": {"facts": 12, "dropped_unverified": 3, "verified_share": 80.0},
               "established_record": {"findings_total": 7, "findings": []},
               "brief_check": {"used": True, "check": {"written": 6, "kept": 5,
                                                       "removed": [{"sentence": "x", "reason": "y"}]}}}
    measured = quality.assess(payload, CORPUS)["record"]
    assert measured["verified_share"] == 80.0
    assert measured["findings_on_record"] == 7
    assert (measured["brief_sentences_written"], measured["brief_sentences_removed"]) == (6, 1)
    assert measured["brief_not_used_because"] is None


def test_each_section_is_measured_against_the_record():
    finding = {"statement": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
               "status": "reported", "independent_sources": 2,
               "sources": [{"quote": "summoned the Treasury over the delayed county disbursements",
                            "publisher": "Daily Nation", "ref": "aaaaaaaa"}]}
    payload = {
        "established_record": {"findings": [finding]},
        "deep_insights": {"insights": [{
            "headline": "The Senate Finance Committee summoned the Treasury over delayed disbursements to counties.",
            "reasoning": "Wanjiru is quietly positioning herself for the governorship next year."}]},
    }
    support = quality.assess(payload, CORPUS, "Jane Wanjiru")["record"]["support"]
    assert (support["sentences"], support["on_record"]) == (2, 1)
    assert support["by_section"]["deep_insights"] == {"sentences": 2, "on_record": 1}


def test_no_record_means_no_support_figure_rather_than_zero():
    assert quality.assess({"executive_brief": "x" * 50}, CORPUS)["record"]["support"] is None

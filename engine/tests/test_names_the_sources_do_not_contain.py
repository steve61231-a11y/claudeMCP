"""A name in the report that is in no source was not reported — it was invented.

The defence against invention was GROUNDING_RULES, a preamble telling every
analyst to use only the supplied material. Quotes get checked against their
ref, so a fabricated quote is dropped. **Prose never was.** An analyst could
write any sentence into the executive brief and nothing mechanical looked at
it before a client read it.

That held while the backend was the model the prompts were tuned against. On
a cheaper one it does not, and the symptom is exactly what you would expect:
a report opening on a sentence about a person nobody has ever mentioned.
This codebase already knows the rule — *a prompt instruction is not a
guarantee* is the comment next to style_check, which exists for this reason.

So check the one thing checkable without a model. Not claims, not numbers —
names, which are mechanical and are what makes a hallucinating report obvious
at a glance.

The hard requirement is the opposite of the obvious one: **it must not cry
wolf.** An operator who learns to ignore this banner has lost it, and a real
report is full of names that look odd but are genuinely in the sources. So
the test is loose on purpose, and most of what follows pins that looseness.
"""

import pytest

from engine.reports import grounding

SOURCES = [
    {"text": "Musalia Mudavadi, the Prime Cabinet Secretary, met officials from "
             "the National Treasury in Nairobi on Tuesday."},
    {"headline": "Mudavadi defends Kenya's foreign policy at Chatham House"},
    {"text": "Reaction from @KenyaDailyNews was sharply critical.",
     "author_handle": "@KenyaDailyNews"},
]


# --- the defect ---------------------------------------------------------------

def test_an_invented_person_is_caught():
    out = grounding.check(
        {"executive_brief": "Peter Kamau sat on a red chair during the session."},
        SOURCES)
    assert "Peter Kamau" in out["ungrounded"]


def test_a_real_subject_is_not_flagged():
    out = grounding.check(
        {"executive_brief": "Musalia Mudavadi defended the policy at Chatham House."},
        SOURCES)
    assert out["ungrounded"] == []


def test_a_surname_grounds_against_the_full_name():
    """The sources say "Musalia Mudavadi"; the report says "Mudavadi". Flagging
    that would make the check useless on every report ever written."""
    out = grounding.check({"executive_brief": "Mudavadi has defended the policy."}, SOURCES)
    assert out["ungrounded"] == []


def test_an_organisation_grounds_on_its_distinctive_word():
    """"the National Treasury" in the sources, "National Treasury" in the
    report — and "National" alone proves nothing, so it is the distinctive
    token that has to carry it."""
    out = grounding.check(
        {"executive_brief": "The National Treasury was represented at the meeting."},
        SOURCES)
    assert out["ungrounded"] == []


def test_prose_at_any_depth_is_checked():
    """A hand-written list of fields is what let raw citations leak for three
    rounds in this codebase. The same mistake here would miss whichever
    section happens to be hallucinating."""
    out = grounding.check({
        "narrative_breakdown": [
            {"label": "Foreign policy",
             "description": "Remarks echoed by Jane Wanjiru at the summit."}],
        "deep_insights": {"insights": [{"reasoning": "Backed by Samuel Otieno."}]},
    }, SOURCES)
    assert "Jane Wanjiru" in out["ungrounded"]
    assert "Samuel Otieno" in out["ungrounded"]


# --- it must not cry wolf -----------------------------------------------------

def test_a_citation_list_is_not_prose():
    """`*_citations` carries resolved URLs and platform names, not sentences.
    Running a name check over it invents flags out of outlet slugs."""
    out = grounding.check({
        "executive_brief": "Mudavadi defended the policy.",
        "executive_brief_citations": [
            {"n": 1, "ref": "abcd1234", "url": "https://nation.africa/Some-Headline-Here",
             "platform": "nation.africa"}],
    }, SOURCES)
    assert out["ungrounded"] == []


def test_a_name_in_a_url_slug_still_grounds_the_report():
    """Outlets put names in paths. If the corpus blob ignored URLs, a report
    citing a story about someone would be flagged for naming them."""
    sources = [{"text": "Coverage was heavy.",
                "source_url": "https://nation.africa/kenya/news/peter-kamau-appointed-1234"}]
    out = grounding.check(
        {"executive_brief": "Peter Kamau was appointed to the role."}, sources)
    assert out["ungrounded"] == []


def test_titles_and_honorifics_alone_do_not_ground_or_accuse():
    """"Cabinet Secretary" is not evidence of anything in either direction —
    it appears in every Kenyan political corpus and names nobody."""
    assert grounding.is_grounded("Cabinet Secretary", "") is True


# --- the honesty rule ---------------------------------------------------------

def test_no_corpus_is_not_a_clean_bill_of_health():
    """Reporting "0 ungrounded" from an examination that never happened is the
    exact failure this codebase exists to prevent."""
    out = grounding.check({"executive_brief": "Peter Kamau did something."}, [])
    assert out["checked"] == 0
    assert "skipped" in out


def test_a_clean_report_says_how_many_names_were_checked():
    """"We looked and found none" has to be distinguishable from "nobody
    looked", and a bare empty list cannot do that."""
    out = grounding.check(
        {"executive_brief": "Musalia Mudavadi defended the policy at Chatham House."},
        SOURCES)
    assert out["ungrounded"] == []
    assert out["checked"] >= 1


def test_a_lone_surname_is_not_treated_as_a_candidate():
    """Without spaCy the extractor needs two capitalised words, so "Mudavadi"
    alone is not picked up. That is the right trade: every sentence starts
    with a capital, and treating single capitalised tokens as names would
    accuse the report of inventing "Tuesday". It errs toward checking fewer
    names rather than toward false alarms, which is the requirement."""
    out = grounding.check({"executive_brief": "Mudavadi defended the policy."}, SOURCES)
    assert out["ungrounded"] == []


def test_the_report_says_which_extractor_ran():
    """`settings.low_memory` — which is the DEFAULT — replaces spaCy's
    pipeline with a blank one that has no NER. A check that silently did
    nothing on a default deployment would hand back a clean grounding result
    nobody had earned."""
    out = grounding.check({"executive_brief": "Mudavadi defended the policy."}, SOURCES)
    assert out["method"] in {"ner", "capitalised-names"}


def test_the_check_survives_a_broken_extractor(monkeypatch):
    """It is a disclosure, not a feature. It must never be the reason a report
    fails to render."""
    import engine.processing.entities as ents

    monkeypatch.setattr(ents, "get_nlp", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    out = grounding.check({"executive_brief": "Peter Kamau did something."}, SOURCES)
    assert "Peter Kamau" in out["ungrounded"], "fell back to nothing instead of the regex"


# --- it reaches the page ------------------------------------------------------

def test_the_api_ships_grounding_to_the_frontend():
    import inspect

    from engine import api_server

    assert '"grounding": payload.get("grounding")' in inspect.getsource(api_server)


def test_the_pipeline_actually_runs_the_check():
    import inspect

    from engine.reports import sections

    assert "grounding.check(" in inspect.getsource(sections)


def test_the_page_renders_the_warning():
    from engine.api_server import render_frontend_document

    page = render_frontend_document()
    # Assert on a contiguous run of text. The sentence a reader sees is built
    # from template expressions ("1 name ... does" / "3 names ... do"), so it
    # does not exist as one string in the source.
    assert "was not reported — it was invented" in page
    assert "r.grounding" in page

"""The framework must read the payload it is actually given.

It was written against `_build_frontend_payload`'s shape — narratives,
influence, summary — and the pipeline hands it the raw payload, which names
those narrative_breakdown, influence_summary and executive_summary. Every read
returned None.

6.0 picks the leading narrative to decide the most important issue, so with
narratives permanently invisible it answered "No dominant issue identified in
this period's coverage" on EVERY report ever produced, regardless of the data.
That is a blank analytical section masquerading as a finding.
"""

from datetime import datetime

from engine.reports import sentiment_framework as fw


def _pipeline_payload():
    """Exactly what engine/pipeline.py passes in."""
    return {
        "sentiment_breakdown": {"positive_pct": 20.0, "neutral_pct": 60.0,
                                "negative_pct": 20.0, "total_mentions_analyzed": 100},
        "volume_trends": {"total_mentions": 109, "by_platform": {"news": 89, "youtube": 20},
                          "by_day": {}},
        "narrative_breakdown": [
            {"label": "Mt Kenya succession", "description": "Consolidation of Mt Kenya "
             "support ahead of 2027 in the space Gachagua vacated.",
             "strength_score": 88.5, "growth_rate": 0.45, "mention_count": 41},
            {"label": "Prisons service reform", "description": "Correctional and "
             "rehabilitation policy appearances.",
             "strength_score": 22.0, "growth_rate": -0.1, "mention_count": 12},
        ],
        "influence_summary": [
            {"author_handle": "ktnnews", "score": 91.2, "sentiment_contribution": -3.0},
            {"author_handle": "citizentv", "score": 77.0, "sentiment_contribution": 1.5},
        ],
        "executive_summary": "Coverage is dominated by the 2027 succession question.",
        "risks": [], "opportunities": [],
    }


def test_the_leading_narrative_is_found_in_the_pipeline_shape():
    """The bug: 6.0 answered "no dominant issue" on every report ever made."""
    payload = _pipeline_payload()
    normalised = fw.normalise_payload(payload)
    section = fw.build_strategic_implications(normalised, {"potential_barriers": []})

    assert section["issue"] == "Mt Kenya succession"
    assert "No dominant issue" not in (section["outline"] or "")
    assert section["trajectory"] == "escalating"  # growth_rate 0.45


def test_current_issues_uses_narratives_when_they_carry_tone():
    payload = _pipeline_payload()
    payload["narrative_breakdown"][0]["tone"] = "negative"
    payload["narrative_breakdown"][1]["tone"] = "positive"
    section = fw.build_current_issues(fw.normalise_payload(payload))

    assert section["potential_barriers"][0]["issue"] == "Mt Kenya succession"
    assert section["potential_levers"][0]["issue"] == "Prisons service reform"


def test_the_executive_summary_is_not_silently_blank():
    payload = _pipeline_payload()
    section = fw.build_summary_of_subject(
        type("P", (), {"name": "Kithure Kindiki", "titles": [], "keywords": [],
                       "subject_type": "politician"})(),
        fw.normalise_payload(payload),
    )
    assert "2027 succession" in section["executive_summary"]


def test_key_people_come_from_the_influence_ranking():
    payload = _pipeline_payload()
    section = fw.build_strategic_implications(
        fw.normalise_payload(payload), {"potential_barriers": []}
    )
    assert "ktnnews" in section["key_people"]


def test_the_frontend_shape_still_works():
    """Both shapes are real; normalising must not break the other caller."""
    frontend = {
        "sentiment_breakdown": {"positive_pct": 10.0, "neutral_pct": 80.0,
                                "negative_pct": 10.0, "total_mentions_analyzed": 50},
        "volume_trends": {"total_mentions": 50, "by_platform": {}, "by_day": {}},
        "narratives": [{"label": "Already frontend shaped", "description": "d",
                        "strength": 50, "growth": 0.1, "mentions": 10}],
        "influence": [{"who": "someone", "score": 10, "sentiment": 0}],
        "executiveBrief": "brief text",
    }
    normalised = fw.normalise_payload(frontend)
    assert normalised["narratives"][0]["strength"] == 50
    assert normalised["influence"][0]["who"] == "someone"
    assert normalised["summary"] == "brief text"

    section = fw.build_strategic_implications(normalised, {"potential_barriers": []})
    assert section["issue"] == "Already frontend shaped"


def test_an_empty_payload_still_says_so_honestly():
    """When there genuinely is no narrative, "no dominant issue" is correct."""
    section = fw.build_strategic_implications(
        fw.normalise_payload({"narrative_breakdown": []}), {"potential_barriers": []}
    )
    assert section["issue"] is None
    assert "No dominant issue" in section["outline"]

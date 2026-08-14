"""Sentiment Analysis Framework V1.0 — conformance tests.

The framework document is the contract. These tests check the output matches it
exactly: every numbered parameter present, its own terminology preserved
("potential levers"/"potential barriers"), its stated caps honoured (3 issues,
72-hour window, ~100 engagement threshold), and its definitions applied as
written rather than reinterpreted.
"""

from datetime import datetime, timedelta

from engine.db.models import Politician
from engine.reports import sentiment_framework as sf


def _subject(**kwargs):
    defaults = dict(name="John Mbadi", aliases=["Mbadi"], titles=["Treasury CS"],
                    keywords=["Kenya", "Treasury"], swahili_terms=[],
                    subject_type="politician")
    defaults.update(kwargs)
    return Politician(**defaults)


def _payload(**kwargs):
    base = {
        "sentiment_breakdown": {"positive_pct": 30.0, "negative_pct": 50.0,
                                "neutral_pct": 20.0, "total_mentions_analyzed": 100},
        "executiveBrief": "A brief.",
        "narratives": [],
        "risks": [], "opportunities": [], "timeline": [], "influence": [],
    }
    base.update(kwargs)
    return base


def _mention(platform, hours_ago=1, engagement=None, source_type="post", text="text"):
    return {
        "platform": platform, "source_type": source_type, "text": text,
        "posted_at": datetime.utcnow() - timedelta(hours=hours_ago),
        "engagement": engagement or {}, "source_url": "https://x/y",
    }


# --- 1.0 Summary of subject -------------------------------------------------

def test_position_applies_to_individuals_only():
    """The framework specifies position '(for individuals)'."""
    person = sf.build_summary_of_subject(_subject(), _payload())
    company = sf.build_summary_of_subject(
        _subject(name="Acme Ltd", subject_type="business", titles=[]), _payload())

    assert person["position"] == "Treasury CS"
    assert company["position"] is None
    assert "organisation/company" in company["who_they_are"]


def test_executive_summary_covers_parameters_three_four_five():
    """'An executive summary of findings in 3, 4, and 5.'"""
    section = sf.build_summary_of_subject(_subject(), _payload())
    joined = " ".join(section["covers_parameters"])
    assert "3.0" in joined and "4.0" in joined and "5.0" in joined


# --- 2.0 Sentiment score ----------------------------------------------------

def test_sentiment_score_is_share_of_positive_as_defined():
    assert sf.sentiment_score(positive=30, negative=50, neutral=20) == 30.0
    assert sf.sentiment_score(0, 0, 0) == 0.0


def test_score_definition_travels_with_the_number():
    """The metric was flagged provisional, so its meaning must be visible."""
    section = sf.build_sentiment_score_section(_payload(), previous=None)
    assert "positive" in section["definition"].lower()


def test_previous_score_and_direction_are_reported():
    previous = {"sentiment_framework": {"sentiment_score": {"score": 20.0}}}
    section = sf.build_sentiment_score_section(_payload(), previous=previous)

    assert section["previous_score"] == 20.0
    assert section["change"] == 10.0
    assert section["direction"] == "improving"


def test_first_period_says_so_rather_than_implying_change():
    section = sf.build_sentiment_score_section(_payload(), previous=None)
    assert section["previous_score"] is None
    assert section["direction"] == "no prior period"


# --- 2.0 Overall mentions ---------------------------------------------------

def test_outlet_segmentation_splits_local_international_and_social():
    assert sf.classify_outlet("twitter") == "social_media"
    assert sf.classify_outlet("news", "nation.africa") == "local_media"
    assert sf.classify_outlet("news", "citizen.digital") == "local_media"
    assert sf.classify_outlet("news", "anything.co.ke") == "local_media"
    assert sf.classify_outlet("news", "bbc.com") == "international_media"


def test_percentage_difference_from_previous_period():
    """'So if they run for a month, we tell them how much it changed.'"""
    mentions = [_mention("twitter") for _ in range(150)]
    previous = {"sentiment_framework": {"overall_mentions": {"total": 100}}}

    section = sf.build_overall_mentions(_payload(), mentions, previous)

    assert section["total"] == 150
    assert section["difference_pct"] == 50.0


def test_covered_sources_are_disclosed():
    """'We need to provide clarity to the client on which sites/apps are covered.'"""
    mentions = [_mention("twitter"), _mention("news", source_type="article")]
    section = sf.build_overall_mentions(_payload(), mentions, None)
    assert "sources_covered" in section
    assert section["sources_covered"]["social_media"] == ["twitter"]


# --- 3.0 Sentiment ----------------------------------------------------------

def test_sentiment_totals_are_counted_from_stored_records():
    sentiments = {f"m{i}": {"sentiment": "positive"} for i in range(3)}
    sentiments.update({f"n{i}": {"sentiment": "negative"} for i in range(2)})

    section = sf.build_sentiment_section(_payload(), sentiments)

    assert section["positive"] == 3 and section["negative"] == 2
    assert section["total_analyzed"] == 5
    assert section["chart"] == "pie"  # the framework specifies a pie chart


def test_counts_fall_back_to_payload_percentages():
    """Percentages are all the stored payload carries, so counts derive from them."""
    section = sf.build_sentiment_section(_payload(), sentiments=None)
    assert section["positive"] == 30 and section["negative"] == 50 and section["neutral"] == 20


# --- 4.0 Current issues -----------------------------------------------------

def test_levers_and_barriers_keep_the_frameworks_own_naming():
    payload = _payload(
        opportunities=["Budget credibility is rising", "Youth engagement improving"],
        risks=["Corruption allegations persist"],
    )
    section = sf.build_current_issues(payload)

    assert "potential_levers" in section and "potential_barriers" in section
    assert section["potential_levers"][0]["type"] == "lever"
    assert section["potential_barriers"][0]["type"] == "barrier"


def test_current_issues_are_capped_at_three_per_side():
    """'we can stick to 3 maximum'"""
    payload = _payload(opportunities=[f"opp {i}" for i in range(10)],
                       risks=[f"risk {i}" for i in range(10)])
    section = sf.build_current_issues(payload)

    assert len(section["potential_levers"]) == 3
    assert len(section["potential_barriers"]) == 3


# --- 5.0 Emergent issues ----------------------------------------------------

def test_emergent_window_is_seventy_two_hours():
    inside = _mention("twitter", hours_ago=10, engagement={"likes": 500})
    outside = _mention("twitter", hours_ago=100, engagement={"likes": 500})

    section = sf.build_emergent_issues([inside, outside])

    assert section["window_hours"] == 72
    assert section["count"] == 1


def test_social_items_must_clear_the_engagement_threshold():
    """'perhaps any posts that have garnered significant engagement such as
    over 100 likes/dislikes for social media'"""
    loud = _mention("twitter", hours_ago=5, engagement={"likes": 250})
    quiet = _mention("twitter", hours_ago=5, engagement={"likes": 3})

    section = sf.build_emergent_issues([loud, quiet])

    assert section["engagement_threshold"] == 100
    assert section["count"] == 1
    assert section["items"][0]["qualified_by"] == "engagement"


def test_editorial_coverage_is_not_judged_by_social_engagement():
    """The framework is explicit that traditional media is harder to quantify —
    so a newspaper piece is not discarded for lacking likes."""
    article = _mention("nation.africa", hours_ago=5, engagement={}, source_type="article")
    section = sf.build_emergent_issues([article])

    assert section["count"] == 1
    assert section["items"][0]["qualified_by"] == "editorial coverage"


# --- 6.0 Strategic implications ---------------------------------------------

def test_strategic_implications_cover_outline_status_trajectory_dates_people():
    payload = _payload(
        narratives=[{"label": "Budget scrutiny", "description": "d", "strength": 90,
                     "mentions": 20, "growth": 0.5}],
        timeline=[{"date": "2026-05-03", "event": "Budget tabled"}],
        influence=[{"who": "ntvkenya"}],
    )
    section = sf.build_strategic_implications(payload, sf.build_current_issues(payload))

    assert section["issue"] == "Budget scrutiny"
    assert section["trajectory"] == "escalating"
    assert section["key_dates"][0]["event"] == "Budget tabled"
    assert "ntvkenya" in section["key_people"]


def test_absent_dominant_issue_is_stated_not_invented():
    section = sf.build_strategic_implications(_payload(), {"potential_barriers": []})
    assert section["issue"] is None
    assert "No dominant issue" in section["outline"]


# --- whole framework --------------------------------------------------------

def test_every_framework_parameter_is_present_and_ordered():
    result = sf.build(_subject(), _payload(), [_mention("twitter")])

    assert list(result.keys()) == [
        "framework", "generated_at",
        "summary_of_subject",      # 1.0
        "sentiment_score",         # 2.0
        "overall_mentions",        # 2.0
        "sentiment",               # 3.0
        "current_issues",          # 4.0
        "emergent_issues",         # 5.0
        "strategic_implications",  # 6.0
    ]
    assert result["framework"] == "Sentiment Analysis Framework V1.0"

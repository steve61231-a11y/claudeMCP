"""The deliverable must carry the evidence, not just the counts.

A run produces verbatim citizen quotes, storyline deep-dives, who is driving
each narrative and how each side frames it. The framework rendered an issue as
one generic sentence — "a growing negative narrative" — while the sentences
that make it worth reading sat unused in the same payload.

That is the difference between a report a client pays for and one they could
have written from a headline.
"""

from engine.reports import sentiment_framework as fw


def _payload():
    return {
        "sentiment_breakdown": {"positive_pct": 18.0, "neutral_pct": 62.0,
                                "negative_pct": 20.0, "total_mentions_analyzed": 60},
        "volume_trends": {"total_mentions": 60, "by_platform": {}, "by_day": {}},
        "narrative_breakdown": [
            {"label": "Mt Kenya succession", "description": "Consolidation ahead of 2027.",
             "strength_score": 88.0, "growth_rate": 0.45, "mention_count": 41,
             "tone": "negative"},
        ],
        "narrative_deep_dives": [
            {"label": "Mt Kenya succession",
             "how_it_unfolded": "Began as scattered county-level endorsements and "
                                "consolidated once Murkomen made it explicit.",
             "who_is_driving_it": ["@ktnnews", "@citizentv"],
             "supporter_framing": "Orderly succession behind a competent technocrat.",
             "critic_framing": "A region being handed a leader it did not choose.",
             "quotes": [{"ref": "a1b2", "text": "Mlima Kenya haitakubali kupewa mtu hatujui."}],
             "origin": {"first_seen": {"date": "2026-07-02", "platform": "youtube"},
                        "peak": {"date": "2026-08-20"}, "platforms_reached": 3}},
        ],
        "public_voice": {
            "critical": [
                {"theme": "Mt Kenya grassroots scepticism",
                 "summary": "Doubt that Kindiki carries the region.",
                 "quotes": [{"ref": "c3d4", "text": "Kindiki ni msomi lakini hana grassroots."}]},
            ],
            "supportive": [], "neutral": [],
        },
        "deep_insights": {"the_one_thing": "The endorsement is elite-led, not grassroots."},
        "timeline": [{"date": "2026-08-20", "event": "E" * 150,
                      "quotes": [{"ref": "e5f6", "text": "quote"}], "mentions_that_day": 12}],
        "influence_summary": [{"author_handle": "ktnnews", "score": 91.0}],
        "risks": [], "opportunities": [],
    }


def test_a_barrier_carries_who_drives_it_and_both_framings():
    section = fw.build_current_issues(fw.normalise_payload(_payload()))
    barrier = section["potential_barriers"][0]
    evidence = barrier["evidence"]

    assert "@ktnnews" in evidence["driven_by"]
    assert "Orderly succession" in evidence["supporter_framing"]
    assert "did not choose" in evidence["critic_framing"]
    assert "consolidated once Murkomen" in evidence["how_it_unfolded"]


def test_a_barrier_carries_what_people_actually_said():
    """A quote from a real person is the thing a reader cannot get elsewhere."""
    section = fw.build_current_issues(fw.normalise_payload(_payload()))
    evidence = section["potential_barriers"][0]["evidence"]
    quoted = " ".join(q["text"] for q in evidence["quotes"] + evidence.get("public_voice", []))
    assert "haitakubali kupewa mtu hatujui" in quoted
    assert "hana grassroots" in quoted


def test_citizen_quotes_keep_the_stance_they_came_from():
    section = fw.build_current_issues(fw.normalise_payload(_payload()))
    voice = section["potential_barriers"][0]["evidence"]["public_voice"]
    assert voice[0]["stance"] == "critical"


def test_the_strategic_section_carries_the_one_thing():
    """The single read the whole report exists to produce was generated and
    left out of the deliverable."""
    section = fw.build_strategic_implications(
        fw.normalise_payload(_payload()), {"potential_barriers": []}
    )
    assert "elite-led, not grassroots" in section["the_one_thing"]
    assert section["evidence"]["driven_by"]


def test_key_dates_keep_the_briefing_not_just_the_headline():
    section = fw.build_strategic_implications(
        fw.normalise_payload(_payload()), {"potential_barriers": []}
    )
    date = section["key_dates"][0]
    assert len(date["event"]) > 100, "the mini-briefing was truncated to a headline"
    assert date["quotes"]


def test_an_issue_with_no_deep_dive_degrades_quietly():
    """Evidence is additive. Its absence must not empty the section."""
    payload = _payload()
    payload["narrative_deep_dives"] = []
    payload["public_voice"] = {"critical": [], "supportive": [], "neutral": []}
    section = fw.build_current_issues(fw.normalise_payload(payload))
    barrier = section["potential_barriers"][0]
    assert barrier["issue"] == "Mt Kenya succession"
    assert barrier["evidence"] == {}


def test_the_page_renders_the_evidence():
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text(encoding="utf-8")
    assert "function fwEvidence(" in html
    assert "In their own words" in html
    assert "si.the_one_thing" in html

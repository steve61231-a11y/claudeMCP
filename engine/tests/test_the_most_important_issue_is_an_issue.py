"""6.0 chose "Playful reactions thread" as the most important issue.

A live report on Rigathi Gachagua, under "Strategic implications":

    PLAYFUL REACTIONS THREAD — escalating
    12 mention(s) in period; strength 12.05
    KEY PEOPLE: Ruto · null · 10 co-mentions, William Ruto · President,
                Gachagua · politician, Raila / Raila Odinga, Rigathi Gachagua…
    ACCOUNTS AMPLIFYING IT: Facebook, Daily Nation…

Every line wrong in its own way. The issue was a thread of emoji and
laughter under a video, picked because it was the largest cluster. "Strength
12.05" was the mention count again. "Escalating" was measured by splitting
the storyline at its own median timestamp — half the items fall on each side
of a median by definition — so it moved only when undated items piled onto
the last day. The people were the report's, not the issue's: the subject
twice, one man twice, and a role that was the word "null". The accounts were
the report's loudest, under every issue whatever it was about. And 4.0
skipped every storyline on every live report, because storylines reached it
without their mentions or their tone.
"""

from datetime import datetime, timedelta

from engine.db.models import Politician
from engine.intelligence.narratives import _growth
from engine.reports import sentiment_framework as fw
from engine.reports import storylines

END = datetime(2026, 10, 20, 12)
START = END - timedelta(days=30)


def _m(mid, text, *, kind="article", outlet=None, platform="news", days_ago=3,
       author=None, estimated=False):
    return {"id": mid, "kind": "mention", "platform": platform, "source_type": kind,
            "text": text, "outlet": outlet, "author_handle": author or outlet,
            "posted_at": END - timedelta(days=days_ago), "engagement": {},
            "source_url": f"https://example.org/{mid}", "date_estimated": estimated}


def _corpus():
    """Forty reactions in a thread, a dozen articles about a court case."""
    thread = [_m(f"r{i}", "😂😂 hahaha", kind="comment", platform="youtube",
                 author=f"@viewer{i}", days_ago=2) for i in range(40)]
    court = [_m(f"c{i}", f"High Court hears impeachment appeal, William Ruto and Raila Odinga "
                          f"named; Gachagua's lawyers argue day {i}",
                outlet=("Daily Nation", "The Star", "Citizen Digital", "Reuters")[i % 4],
                days_ago=20 - i) for i in range(12)]
    court.append(_m("c-ruto", "Ruto comments on the impeachment appeal", outlet="The Standard",
                    days_ago=1))
    return thread, court


def _payload(thread, court, **extra):
    return {
        "narrative_breakdown": [
            {"label": "Playful reactions thread", "description": "Emoji and laughter.",
             "strength_score": 40.4, "mention_count": len(thread),
             "mention_ids": [m["id"] for m in thread], "growth_rate": None},
            {"label": "Impeachment appeal", "description": "The High Court appeal.",
             "strength_score": 13.0, "mention_count": len(court),
             "mention_ids": [m["id"] for m in court], "growth_rate": 1.4,
             "growth_basis": "measured", "growth_halves": {"first": 5, "second": 12}},
        ],
        "network_insights": {"top_people": [
            {"name": "Ruto", "role": "null", "co_mentions": 10},
            {"name": "William Ruto", "role": "President", "co_mentions": 30},
            {"name": "Gachagua", "role": "politician", "co_mentions": 50},
            {"name": "Rigathi Gachagua", "role": "Former Deputy President", "co_mentions": 60},
            {"name": "Raila", "role": "None", "co_mentions": 4},
            {"name": "Raila Odinga", "role": "ODM leader", "co_mentions": 12},
        ]},
        "influence_summary": [{"author_handle": "Facebook", "score": 99}],
        "sentiment_breakdown": {"positive_pct": 10, "negative_pct": 50, "neutral_pct": 40,
                                "total_mentions_analyzed": 52},
        "volume_trends": {"total_mentions": 53, "by_platform": {}, "by_day": {}},
        "timeline": [
            {"date": "2026-10-05", "event": "Budget committee tables the supplementary estimates"},
            {"date": "2026-10-10", "event": "High Court admits the impeachment appeal for hearing"},
        ],
        "risks": [], "opportunities": [],
        **extra,
    }


def _subject():
    return Politician(name="Rigathi Gachagua", aliases=["Riggy G"], titles=[], keywords=[],
                      swahili_terms=[], subject_type="politician")


def _sentiments(thread, court):
    scores = {m["id"]: {"sentiment": "neutral"} for m in thread}
    for i, m in enumerate(court):
        scores[m["id"]] = {"sentiment": "negative" if i % 3 else "positive"}
    return scores


def _built():
    thread, court = _corpus()
    return fw.build(_subject(), _payload(thread, court), thread + court,
                    sentiments=_sentiments(thread, court), now=END)


def test_a_reaction_thread_is_not_the_most_important_issue():
    six = _built()["strategic_implications"]
    assert six["issue"] == "Impeachment appeal"
    assert "published" in six["chosen_because"]
    aside = {a["label"]: a["reason"] for a in six["set_aside"]}
    assert "Playful reactions thread" in aside, "an exclusion must be declared"
    assert "comments" in aside["Playful reactions thread"]


def test_the_status_is_counts_never_a_second_name_for_the_count():
    six = _built()["strategic_implications"]
    assert "strength" not in six["status"]
    assert "13 mention(s)" in six["status"] and "outlet" in six["status"]
    assert "negative" in six["status"], "the status says which way it leans"


def test_people_are_the_issue_s_merged_without_the_subject_or_a_null_role():
    people = _built()["strategic_implications"]["key_people"]
    names = [p["name"] for p in people]
    assert "William Ruto" in names and "Ruto" not in names, "one man, one row"
    assert "Raila Odinga" in names and "Raila" not in names
    assert not any("Gachagua" in n for n in names), "the subject is not a person in his own story"
    assert all(p["role"] not in ("null", "None", "none") for p in people)
    ruto = next(p for p in people if p["name"] == "William Ruto")
    assert ruto["named_in"] == 13 and ruto["of"] == 13, "counted in THIS issue's items"


def test_who_carried_it_is_the_issue_s_publishers_not_the_report_s():
    amplifiers = {a["handle"]: a for a in _built()["strategic_implications"]["key_amplifiers"]}
    assert "Facebook" not in amplifiers
    assert amplifiers["Daily Nation"]["items"] == 3
    assert amplifiers["Reuters"]["outlet_type"] == "international_media"


def test_key_dates_are_the_issue_s_own_and_say_what_happened():
    dates = _built()["strategic_implications"]["key_dates"]
    kinds = {d["kind"] for d in dates}
    assert {"first", "latest"} <= kinds
    first = next(d for d in dates if d["kind"] == "first")
    assert first["items"] and first["items"][0]["publisher"], "a date with nothing under it"
    briefings = [d["event"] for d in dates if d["kind"] == "briefing"]
    assert any("impeachment appeal" in e for e in briefings)
    assert not any("supplementary estimates" in e for e in briefings), \
        "a briefing about another issue is not this issue's date"


def test_trajectory_carries_the_counts_and_is_absent_when_unmeasured():
    six = _built()["strategic_implications"]
    assert six["trajectory"] == "escalating"
    assert "12 dated mention(s) in the second half" in six["trajectory_basis"]
    unmeasured = fw.trajectory({"growth": None, "growth_basis": "too_few_mentions",
                                "growth_halves": {"first": 1, "second": 2}})
    assert unmeasured[0] is None and "too few" in unmeasured[1]
    new = fw.trajectory({"growth": None, "growth_basis": "no_earlier_half",
                         "growth_halves": {"first": 0, "second": 9}})
    assert new[0] == "new"


def test_growth_splits_the_period_by_time_and_ignores_undated_items():
    def at(days_ago, estimated=False):
        return {"posted_at": END - timedelta(days=days_ago), "date_estimated": estimated}

    # Three early, nine late: a median split called this flat.
    items = [at(25), at(24), at(20)] + [at(d) for d in (9, 8, 7, 6, 5, 4, 3, 2, 1)]
    rate, basis, halves = _growth(items, (START, END))
    assert basis == "measured" and halves == {"first": 3, "second": 9,
                                              "split_at": halves["split_at"]}
    assert rate == 2.0
    # The undated pile stamped on the last day is not a surge.
    flat = [at(25), at(24), at(20), at(5), at(4), at(3)] + [at(0, True)] * 30
    rate, basis, halves = _growth(flat, (START, END))
    assert halves["second"] == 3 and basis == "too_few_mentions"


def test_levers_and_barriers_come_from_the_storylines_own_tone():
    four = _built()["current_issues"]
    barriers = [b["issue"] for b in four["potential_barriers"]]
    assert barriers == ["Impeachment appeal"]
    assert four["potential_barriers"][0]["profile"]["tone"]["negative"] == 8
    assert "barriers" not in four["from_analysts"]
    assert any(a["label"] == "Playful reactions thread" for a in four["set_aside"])


def test_sentiment_is_split_by_outlet_over_time_and_by_driver():
    three = _built()["sentiment"]
    segments = {s["outlet_type"]: s for s in three["by_segment"]}
    assert segments["international_media"]["scored"] == 3
    assert segments["social_media"]["neutral"] == 40
    assert three["over_time"]["granularity"] == "day"
    assert sum(p["negative"] for p in three["over_time"]["series"]) == 8
    driver = three["drivers"]["negative"][0]
    assert driver["issue"] == "Impeachment appeal" and driver["count"] == 8


def test_volume_by_day_counts_only_dated_items():
    thread, court = _corpus()
    court.append(_m("undated", "no date", outlet="Daily Nation", days_ago=0, estimated=True))
    built = fw.build(_subject(), _payload(thread, court), thread + court,
                     sentiments=_sentiments(thread, court), now=END)
    two = built["overall_mentions"]
    assert two["dated"] == len(thread) + len(court) - 1
    assert two["peak"]["count"] == 40, "the thread's day is the busiest"


def test_emergent_issues_are_coverage_not_reactions_or_undated_items():
    thread, court = _corpus()
    loud = dict(thread[0], engagement={"likes": 5000})
    undated = _m("u", "undated story", outlet="Daily Nation", days_ago=0, estimated=True)
    five = fw.build_emergent_issues([loud, undated] + court, now=END)
    ids = {i["mention_id"] for i in five["items"]}
    assert "r0" not in ids and "u" not in ids
    assert "c-ruto" in ids


def test_a_storyline_named_lightly_but_reported_by_newsrooms_is_kept():
    items = [_m(f"n{i}", "Memes mocking the Finance Bill spread", outlet="The Star")
             for i in range(5)]
    narrative = {"label": "Memes mocking the Finance Bill", "mention_ids": [m["id"] for m in items]}
    prof = storylines.profile(narrative, storylines.index_corpus(items), {}, 5)
    assert storylines.is_chatter(narrative, prof) is None


def test_the_pipeline_hands_storylines_over_with_their_mentions():
    from engine.reports.generator import generate_report_payload

    mentions = [{"id": 1, "platform": "news", "author_handle": "a", "text": "t",
                 "posted_at": END, "engagement": {}, "source_type": "article"}]
    narratives = [{"label": "L", "description": "d", "mention_ids": [1], "strength_score": 1.0,
                   "growth_rate": None, "growth_basis": "too_few_mentions", "evidence": []}]
    payload = generate_report_payload("X", START, END, mentions, {}, narratives, [], {})
    assert payload["narrative_breakdown"][0]["mention_ids"] == [1]

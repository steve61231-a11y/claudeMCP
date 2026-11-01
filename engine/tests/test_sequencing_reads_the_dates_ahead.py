"""Sequencing reads the dates ahead that the sources state.

An issue map reads a look-back window, so its sequencing section had nothing
ahead to move against and filled itself with the newest items — on a live
map, YouTube comments, as "Move while this is still live: A YouTube
commenter…". The founder: "the sequencing section needs to look for future
dates like the next hearing, sitting of the NA committee on commerce etc."
Those dates are printed in the sources; a date a source printed is a fact,
carried with its source. Nothing is inferred.
"""

from datetime import date, datetime

from engine.evidence import upcoming
from engine.reports import issue_framework

ITEM = {"id": "abcd1234ffff", "platform": "nation.africa", "source_url": "https://n.example/a",
        "posted_at": datetime(2026, 10, 20),
        "text": ("The High Court will hear the petition on 12 November 2026. Kenya won the case on "
                 "3 March 2026. The committee is expected to sit on Tuesday, November 4. "
                 "Fans celebrated on 20 December. The House resumes next week.")}


def test_only_a_stated_date_ahead_that_names_an_event_is_read():
    rows = upcoming.scan([ITEM], today=date(2026, 10, 31))
    assert [(r["date"], r["event"][:20]) for r in rows] == [
        ("2026-11-04", "The committee is exp"), ("2026-11-12", "The High Court will ")]
    assert rows[0]["url"] == "https://n.example/a" and rows[0]["basis"] == "stated_in_source"


def test_an_undated_source_places_a_yearless_date_from_today_not_from_a_guess():
    undated = dict(ITEM, date_estimated=True, text="The committee will sit on 2 January.")
    rows = upcoming.scan([undated], today=date(2026, 10, 31))
    assert rows[0]["date"] == "2027-01-02" and rows[0]["printed"] is None


def test_sequencing_puts_stated_dates_first_and_no_reaction_is_a_decision_point():
    stated = upcoming.scan([ITEM], today=date(2026, 10, 31))
    out = issue_framework.build_sequencing({"timeline_of_major_developments": []},
                                           now=datetime(2026, 10, 31), upcoming=stated)
    assert out["looking"] == "forward"
    assert out["engagement_timeline"][0]["date"] == "2026-11-04"
    assert "Build alignment ahead of" in out["coalition_windows"][0]["action"]

    past = {"timeline_of_major_developments": [
        {"date": "2026-10-14", "event": "The Court of Appeal rejected the appeal."},
        {"date": "2026-10-15", "event": "A YouTube commenter criticised the coverage."}]}
    out = issue_framework.build_sequencing(past, now=datetime(2026, 10, 31))
    assert out["looking"] == "backward"
    assert all("commenter" not in w["action"] for w in out["coalition_windows"])
    assert out["coalition_windows"], "the ruling is still a decision point"

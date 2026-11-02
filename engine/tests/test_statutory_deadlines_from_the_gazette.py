"""Statutory deadlines from the Kenya Gazette — read from its words, never guessed.

Phase 4 of docs/ISSUE_MAP_FOUNDATION.md asked for the Gazette's deadlines as
dated next steps. The probe (`scripts/gazette_probe.py`, three rounds) found
that Kenya Law's search returns Gazette ISSUES with `nature=Gazette`, holds
them to recent dates, and shows passages of each; the issue itself is
refused to a server on both hosts. So a deadline is read from one passage
that names the issue and states it — a calendar date beside deadline words,
or "within N days" of the notice itself, counted and labelled as counted.
The card texts below are shaped as the probe printed them.
"""

from __future__ import annotations

from datetime import date, datetime

from engine.evidence import gazette_deadlines as gd
from engine.reports import issue_match

TODAY = date(2026, 11, 2)
SHA = issue_match.of("Social Health Authority")


def _card(text, when=datetime(2026, 10, 30), no=190):
    return {"title": f"Kenya Gazette Vol. CXXVIII-No. {no}", "date": when, "kind": "gazette",
            "url": f"https://new.kenyalaw.org/akn/ke/officialGazette/{when:%Y-%m-%d}/{no}/eng@{when:%Y-%m-%d}",
            "card_text": f"Kenya Gazette Vol. CXXVIII-No. {no} {when:%-d %B %Y} " + text + " Download"}


def test_a_stated_date_beside_deadline_words_is_a_deadline():
    card = _card("Page 3 : [...] the Social Health Authority invites written memoranda on the draft "
                 "regulations, to be submitted on or before Friday, 20th November, 2026. [...]")
    got = gd.read([card], SHA, TODAY)
    assert [g["date"] for g in got] == ["2026-11-20"]
    assert got[0]["basis"] == "stated_in_source" and got[0]["record"] == "gazette"
    assert got[0]["publisher"] == "Kenya Gazette Vol. CXXVIII-No. 190, 30 October 2026"
    assert "on or before Friday, 20th November, 2026" in got[0]["event"]


def test_within_days_of_the_notice_is_counted_and_says_so():
    card = _card("Page 2 : [...] any objection to the Social Health Authority tariff review shall be lodged "
                 "within thirty (30) days from the date of this notice [...]", when=datetime(2026, 10, 16))
    got = gd.read([card], SHA, TODAY)
    assert [g["date"] for g in got] == ["2026-11-15"]
    assert got[0]["basis"] == "counted_from_publication"
    assert "counted: 30 days from this notice's publication on 16 October 2026" in got[0]["event"]


def test_a_period_from_some_other_moment_is_not_read():
    """"within 14 days of service" starts on a day nobody printed."""
    card = _card("Page 2 : [...] the Social Health Authority may appeal within fourteen (14) days of service "
                 "of the decision [...]")
    assert gd.read([card], SHA, TODAY) == []


def test_a_deadline_in_a_passage_about_something_else_is_not_the_issues():
    """One Gazette issue carries hundreds of notices: the deadline must be in
    the passage that names the issue, not elsewhere on the card."""
    card = _card("Page 1 : [...] SOCIAL HEALTH AUTHORITY APPOINTMENT IN EXERCISE of the powers conferred [...] "
                 "Page 9 : [...] objections to the land title shall be lodged on or before 30th November, 2026 [...]")
    assert gd.read([card], SHA, TODAY) == []


def test_a_past_deadline_and_a_date_without_deadline_words_are_not_steps_ahead():
    card = _card("Page 3 : [...] the Social Health Authority invites memoranda on or before 20th October, 2026 "
                 "[...] the Social Health Authority was established on 22nd November, 2027 [...]")
    assert gd.read([card], SHA, TODAY) == []


def test_passages_are_split_as_the_card_prints_them():
    parts = gd.fragments("Kenya Gazette No. 1 Page 4 : [...] first passage about something long enough ... "
                         "second passage about another thing entirely [...] Page 5 : [...] a third passage on page five")
    assert [p.split()[0] for p in parts] == ["first", "second", "a"], parts


def test_the_map_asks_the_gazette_filtered_newest_first_and_recent(monkeypatch):
    from engine.config import settings
    from engine.ingestion import kenya_law_records, parliament_records, world_bank_records
    from engine.reports import issue_map

    asked = []

    def search(query, page=1, params=None):
        asked.append(params or {})
        if (params or {}).get("nature") == "Gazette":
            card = _card("Page 3 : [...] the Social Health Authority invites written memoranda on or before "
                         "Friday, 20th November, 2099 [...]")
            return [card], 1, None
        return [], 0, None

    monkeypatch.setattr(settings, "enable_issue_map_records", True)
    monkeypatch.setattr(kenya_law_records, "search", search)
    monkeypatch.setattr(parliament_records, "fetch", lambda *a, **k: ([], []))
    monkeypatch.setattr(world_bank_records, "projects", lambda term: [])
    monkeypatch.setattr(world_bank_records, "documents", lambda term: [])
    report: dict = {}
    _, ahead = issue_map.acquire_records("Ministry of Health", "Social Health Authority", [],
                                         datetime(2026, 10, 1), datetime(2026, 11, 1), ["Ministry of Health"], report)
    gazette = [p for p in asked if p.get("nature") == "Gazette"]
    assert gazette and all(p["ordering"] == "-date" and p["date__gte"] for p in gazette)
    assert [a["record"] for a in ahead] == ["gazette"]
    assert report["gazette"]["deadlines_ahead"] == 1
    assert "not seen" in report["gazette"]["coverage"], "the reach of the reading must be said"


def test_a_refused_gazette_is_said_not_read_as_no_deadline(monkeypatch):
    from engine.config import settings
    from engine.ingestion import kenya_law_records, parliament_records
    from engine.reports import issue_map

    monkeypatch.setattr(settings, "enable_issue_map_records", True)
    monkeypatch.setattr(kenya_law_records, "search",
                        lambda q, page=1, params=None: ([], 0, "HTTP 403: Kenya Law refused this server"))
    monkeypatch.setattr(parliament_records, "fetch", lambda *a, **k: ([], []))
    report: dict = {}
    issue_map.acquire_records("Ministry of Health", "Social Health Authority", [], datetime(2026, 10, 1),
                              datetime(2026, 11, 1), ["Ministry of Health"], report)
    assert report["gazette"]["notes"] and "refused" in report["gazette"]["notes"][0]

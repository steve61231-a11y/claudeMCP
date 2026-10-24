"""What the record shows when read as a whole — counted, so it cannot be invented.

The founder, 24 October 2026: the product has to save a political consultant
the job of going to every platform, and give "insights that could have taken
hours to analyse" — without hallucinating. The insight an analyst earns by
cross-reading is usually in the shape of the coverage, not in one article:
the story everywhere that traces to one newsroom, the claim the press cannot
agree is established, who set the agenda, how much of the record is
accusation. Each is arithmetic over the verified findings
(`evidence/patterns.py`). These tests pin that each pattern fires on the
shape it names, says only numbers it counted, and stays quiet otherwise.
"""

from engine.evidence import patterns
from engine.reports import from_facts


def _src(publisher, day, status="reported", source_type="article", quote="q words here and more"):
    return {"publisher": publisher, "published_at": day, "status": status,
            "source_type": source_type, "quote": quote, "url": f"https://{publisher}.example",
            "ref": publisher[:8]}


def _finding(statement, sources, status="reported", origins=None, syndicated=False):
    return {"statement": statement, "status": status, "sources": sources,
            "publishers": len({s["publisher"] for s in sources}),
            "independent_sources": origins if origins is not None else len(sources),
            "syndicated": syndicated}


ECHO = _finding("Karua met the coalition leaders in Mombasa.",
                [_src("Daily Nation", "2026-10-02"), _src("The Star", "2026-10-02"),
                 _src("Kenyans.co.ke", "2026-10-03")], origins=1, syndicated=True)
CONTESTED = _finding("Karua received campaign funds from a foreign donor.",
                     [_src("Daily Nation", "2026-10-04", status="alleged"),
                      _src("The Standard", "2026-10-05")], status="alleged")
COURT = _finding("Karua filed a petition against the electoral commission.",
                 [_src("Kenya Law", "2026-10-07", source_type="judgment"),
                  _src("Daily Nation", "2026-10-08")])
FINDINGS = [ECHO, CONTESTED, COURT]


def _kinds(found):
    return [p["kind"] for p in found]


def test_a_story_everywhere_that_traces_to_one_report_is_named():
    echo = next(p for p in patterns.detect(FINDINGS, "Martha Karua") if p["kind"] == "echo")
    assert echo["numbers"] == {"outlets": 3, "origins": 1}
    assert "3 outlets" in echo["sentence"] and "single original report" in echo["sentence"]


def test_outlets_disagreeing_on_whether_it_is_established_is_named():
    contested = next(p for p in patterns.detect(FINDINGS) if p["kind"] == "contested")
    assert contested["numbers"] == {"stating": 1, "hedging": 1}


def test_the_share_of_the_record_that_is_accusation_is_counted():
    balance = next(p for p in patterns.detect(FINDINGS, "Martha Karua") if p["kind"] == "balance")
    assert balance["sentence"].startswith("1 of the 3 findings on the record about Martha Karua")


def test_who_broke_the_stories_others_carried():
    agenda = next(p for p in patterns.detect(FINDINGS) if p["kind"] == "agenda")
    assert agenda["sentence"].startswith("Daily Nation was first with 2 of the 3 findings")


def test_what_rests_on_an_official_record_is_counted():
    primary = next(p for p in patterns.detect(FINDINGS) if p["kind"] == "primary")
    assert primary["numbers"] == {"primary": 1, "findings": 3}


def test_a_thin_record_is_not_read_as_a_pattern():
    assert set(_kinds(patterns.detect([COURT]))) <= {"echo", "contested"}
    assert patterns.detect([]) == []


def test_every_number_in_a_pattern_was_counted():
    import re

    for pattern in patterns.detect(FINDINGS, "Martha Karua"):
        stated = {int(n) for n in re.findall(r"\b\d+\b", pattern["sentence"])}
        assert stated <= set(pattern["numbers"].values()), pattern["sentence"]


def test_the_brief_may_cite_a_pattern_and_is_held_to_it():
    ledger = from_facts.build_ledger(FINDINGS)
    entries = from_facts.pattern_entries(patterns.detect(FINDINGS, "Martha Karua"), ledger)
    full = ledger + entries
    balance = next(e for e in entries if "allegations or disputed" in e["statement"])
    good = "One of the three findings on the record about Karua is an allegation, not established."
    assert from_facts.check_sentence(good.replace("One", "1").replace("three", "3"),
                                     [balance["id"]], full, "Martha Karua") is None
    bad = "Seven of the 3 findings on the record about Karua are allegations, not established."
    assert from_facts.check_sentence(bad.replace("Seven", "7"), [balance["id"]], full,
                                     "Martha Karua") is not None
    assert balance["sources"], "a sentence citing a pattern would link to nothing"
    assert "[PATTERN" in from_facts.ledger_text(full)

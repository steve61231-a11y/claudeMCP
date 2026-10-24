"""The timeline's key dates come from the record: when the sources first published.

The timeline analyst chose its own dates and wrote an 80-200 word briefing
for each; only the quotes under it were checked. A date is exactly where an
invented detail does the most damage — "on 14 October Wanjiru met the
Treasury" is a claim a client repeats — and the record already knows, for
every finding, the day its sources first published it.

So the record's key dates are built with no model: each day's event is the
findings first published that day, in their own words, each cited; an
allegation reads as one; the analyst's briefing on a day the record covers
is replaced, keeping its verified reaction quotes; the framework's key
dates carry their citations; and — found on the way — a statement in the
store may not name an identity its own source never raised.
"""

import re
from pathlib import Path

from engine.evidence import facts
from engine.reports import from_facts, protected_terms, sentiment_framework


def _finding(statement, day, origins=1, status="reported", source_type="article", ref="aaaa1111"):
    return {"statement": statement, "status": status, "independent_sources": origins,
            "first_seen": day, "last_seen": day,
            "sources": [{"publisher": "Daily Nation", "url": f"https://{ref}.example", "ref": ref,
                         "published_at": day, "quote": statement.lower().rstrip("."),
                         "source_type": source_type}]}


SUMMONED = _finding("The Senate Finance Committee summoned the Treasury over county disbursements.",
                    "2026-10-14", origins=3, ref="aaaa1111")
DIVERTED = _finding("Opposition senators said the funds were diverted.", "2026-10-14",
                    status="alleged", ref="bbbb2222")
GAZETTED = _finding("Jane Wanjiru was appointed to the Intergovernmental Budget Council.",
                    "2026-10-03", source_type="gazette", ref="cccc3333")
MINOR = _finding("Jane Wanjiru attended a church fundraiser in Kiambu.", "2026-10-09", ref="dddd4444")
BARE_ALLEGATION = _finding("The funds were diverted to a private account.", "2026-10-20",
                           status="alleged", ref="eeee5555")


def test_days_are_the_publication_dates_weightiest_kept_then_in_order():
    days = facts.by_date([SUMMONED, DIVERTED, GAZETTED, MINOR], most_dates=2)
    assert list(days) == ["2026-10-03", "2026-10-14"], \
        "a gazette notice outweighs a single report; the busiest day stays; then date order"
    assert [f["statement"] for f in days["2026-10-14"]] == [SUMMONED["statement"], DIVERTED["statement"]]


def test_an_undated_finding_is_on_no_day():
    undated = {**MINOR, "first_seen": None}
    assert facts.by_date([undated]) == {}


def test_each_day_is_its_findings_cited_with_no_model():
    record = from_facts.timeline_from_record(facts.by_date([SUMMONED, DIVERTED]))
    day = record["2026-10-14"]
    assert day["event"].startswith("The Senate Finance Committee summoned the Treasury")
    assert re.search(r"\[\d+\]", day["event"]) and day["event_citations"]
    assert day["corroboration"] == {"independent_sources": 3, "confirmed": True,
                                    "label": "On the record: 3 independent sources",
                                    "basis": "verified_facts"}
    assert len(day["on_record"]) == 2


def test_an_allegation_under_a_date_reads_as_one():
    record = from_facts.timeline_from_record(facts.by_date([BARE_ALLEGATION]))
    day = record["2026-10-20"]
    assert day["event"].startswith("Alleged: The funds were diverted")
    assert day["corroboration"]["confirmed"] is False
    assert day["corroboration"]["label"] == "On the record: allegations only"


def test_an_attributed_allegation_is_left_as_written():
    record = from_facts.timeline_from_record(facts.by_date([DIVERTED]))
    assert record["2026-10-14"]["event"].startswith("Opposition senators said")


ANALYST_DAY = {"date": "2026-10-14", "event": "An 80-word briefing nobody checked [1].",
               "event_citations": [{"n": 1, "ref": "zzzz"}],
               "quotes": [{"ref": "q1", "text": "They must answer."}], "mentions_that_day": 9}
ANALYST_SAME_DAY = {"date": "2026-10-14", "event": "A second unchecked account.",
                    "quotes": [{"ref": "q2", "text": "Pay the counties."}]}
ANALYST_OTHER_DAY = {"date": "2026-10-11", "event": "A rally the record holds nothing about.",
                     "quotes": [], "mentions_that_day": 4}
ANALYST_CONTEXT = {"date": "2026-10-14", "event": "Context.", "before_period": True}


def test_a_day_on_the_record_replaces_the_briefing_and_keeps_its_quotes():
    record = from_facts.timeline_from_record(facts.by_date([SUMMONED, GAZETTED]))
    merged = from_facts.merge_timeline([ANALYST_DAY, ANALYST_SAME_DAY, ANALYST_OTHER_DAY],
                                       record, {"2026-10-03": 2})
    assert [t["date"] for t in merged] == ["2026-10-03", "2026-10-11", "2026-10-14"]
    replaced = merged[2]
    assert replaced["basis"] == "verified_facts"
    assert "nobody checked" not in replaced["event"]
    assert [q["ref"] for q in replaced["quotes"]] == ["q1", "q2"], "verified reactions are kept"
    assert replaced["mentions_that_day"] == 9
    assert merged[1] is ANALYST_OTHER_DAY, "a day the record does not cover keeps the analyst's entry"
    added = merged[0]
    assert added["basis"] == "verified_facts" and added["mentions_that_day"] == 2


def test_context_before_the_period_is_not_replaced():
    record = from_facts.timeline_from_record(facts.by_date([SUMMONED]))
    merged = from_facts.merge_timeline([ANALYST_CONTEXT], record)
    assert ANALYST_CONTEXT in merged and len(merged) == 2


def test_framework_key_dates_carry_citations_and_say_which_are_on_the_record():
    record = from_facts.timeline_from_record(facts.by_date([SUMMONED]))
    entry = from_facts.merge_timeline([], record)[0]
    key = sentiment_framework._key_date(entry)
    assert key["kind"] == "record" and key["event_citations"]
    assert sentiment_framework._key_date(ANALYST_DAY)["kind"] == "briefing"
    assert sentiment_framework._key_date(ANALYST_DAY)["event_citations"]


def _walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
            if isinstance(value, str) and re.search(r"\[\d+\]", value) and not key.endswith("_citations"):
                yield path, key, node
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


def test_no_prose_reaches_the_framework_without_its_citations():
    """The rule over the whole framework, not field by field: any string the
    framework carries with [n] markers in it has its citation list beside
    it, or the reader sees brackets that lead nowhere."""
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 24)
    mentions = [{"id": f"m{i}", "text": f"Wanjiru and the Treasury, item {i}.", "platform": "nation.africa",
                 "source_type": "article", "author_handle": "Daily Nation",
                 "posted_at": now - timedelta(days=i % 10), "engagement": {}} for i in range(30)]
    record = from_facts.timeline_from_record(facts.by_date([SUMMONED, GAZETTED]))
    payload = {
        "timeline": from_facts.merge_timeline([ANALYST_OTHER_DAY], record),
        "narrative_breakdown": [{"label": "Treasury disbursements standoff",
                                 "description": "The Senate committee, the Treasury and county disbursements.",
                                 "mention_ids": [m["id"] for m in mentions], "mentions": 30}],
        "narrative_deep_dives": [{"label": "Treasury disbursements standoff", "how_it_unfolded": "Summoned [1].",
                                  "how_it_unfolded_citations": [{"n": 1, "ref": "aaaa1111"}],
                                  "supporter_framing": "Fair [1].",
                                  "supporter_framing_citations": [{"n": 1, "ref": "aaaa1111"}],
                                  "critic_framing": "Late [1].",
                                  "critic_framing_citations": [{"n": 1, "ref": "aaaa1111"}]}],
        "volume_trends": {"total_mentions": 30, "by_day": {}},
    }
    from engine.db.models import Politician

    subject = Politician(name="Jane Wanjiru", aliases=["Wanjiru"], titles=["Senator"],
                         keywords=["Kenya"], swahili_terms=[], subject_type="politician")
    built = sentiment_framework.build(subject, payload, mentions)
    marked = list(_walk(built))
    reached = {key for _path, key, _node in marked}
    assert {"how_it_unfolded", "event"} <= reached, \
        f"the rule reached only {sorted(reached)} — a rule that finds nothing passes vacuously"
    bare = [(path, key) for path, key, node in marked if not node.get(f"{key}_citations")]
    assert not bare, f"prose with markers and no citations: {bare[:5]}"


def test_a_statement_may_not_name_an_identity_its_source_never_raised():
    source = "The senator told the rally in Kisumu the levy was unfair."
    assert protected_terms.introduced("The Luo senator told the rally the levy was unfair.", source) == \
        ("luo", "ethnicity")
    assert protected_terms.introduced("The senator told the rally the levy was unfair.", source) is None
    assert protected_terms.introduced("The Luo senator spoke.", source + " Luo leaders attended.") is None


def test_the_store_does_not_keep_a_statement_that_adds_an_identity():
    item = {"id": "x1", "text": "The senator told the rally in Kisumu the levy was unfair to traders.",
            "full_text": True}
    entries = [{"statement": "The Luo senator said the levy was unfair to traders.",
                "quote": "the levy was unfair to traders", "kind": "fact", "status": "reported"},
               {"statement": "The senator said the levy was unfair to traders.",
                "quote": "the levy was unfair to traders", "kind": "fact", "status": "reported"}]
    kept, dropped = facts.verify(item, entries, 6000)
    assert [k["statement"] for k in kept] == ["The senator said the levy was unfair to traders."]
    assert dropped == 1, "the addition is counted with the rest of what failed"


def test_the_timeline_page_shows_the_record_for_a_day_and_cleans_its_tooltip():
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    at = html.index("function renderDeepTimeline(")
    body = html[at:at + 5000]
    assert "recordRow(" in body and "Analyst briefing" in body
    strip = html[html.index("function interactiveTimelineStrip("):]
    strip = strip[:strip.index("wrap.appendChild(svg)")]
    assert "plainEvent(p.t)" in strip and "p.t.event)||'').slice" not in strip

""""Beneath the surface" is read from the record, and its strength is counted.

The founder, 24 October 2026: insights "that could have taken hours to
analyse", without hallucinating. The section that promised them was the
digest analyst's: asked for contradictions, coordinated amplification and
"the detail that reframes everything", and left to rate its own confidence
high, medium or low. Nothing checked either. A pattern it described could
be one it imagined, and "high" said the model was sure — not that the
sources agreed.

Now the reading is written from the verified findings and the patterns
counted across them (`from_facts.insights_from_record`), each headline and
line of reasoning passes the brief's sentence check, the implication is a
labelled judgement that may not add names, figures or dates, and the
strength beside each insight is COMPUTED from what it cites.
"""

from pathlib import Path

from engine import llm
from engine.evidence import patterns
from engine.reports import from_facts

SUBJECT = "Jane Wanjiru"

FINDINGS = [
    {"statement": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
     "status": "reported", "independent_sources": 3, "first_seen": "2026-10-14",
     "sources": [{"publisher": "Daily Nation", "url": "https://n.example/1", "ref": "aaaa1111",
                  "quote": "summoned the Treasury over the delayed county disbursements"},
                 {"publisher": "The Standard", "url": "https://s.example/1", "ref": "bbbb2222",
                  "quote": "Treasury officials were summoned by the committee"}]},
    {"statement": "Jane Wanjiru said fourteen counties could not pay health workers.",
     "status": "reported", "independent_sources": 1, "first_seen": "2026-10-15",
     "sources": [{"publisher": "Daily Nation", "url": "https://n.example/2", "ref": "cccc3333",
                  "quote": "14 counties unable to pay health workers"}]},
    {"statement": "Opposition senators alleged that the funds were diverted to a private account.",
     "status": "alleged", "independent_sources": 1, "first_seen": "2026-10-16",
     "sources": [{"publisher": "The Star", "url": "https://t.example/1", "ref": "dddd4444",
                  "quote": "alleged that the funds had been diverted to a private account"}]},
]
RECORD = {"findings": FINDINGS, "patterns": patterns.detect(FINDINGS, SUBJECT)}


def _writer(monkeypatch, insights, one=None):
    seen = []

    def answer(instructions, untrusted, expected_keys, **kw):
        seen.append((instructions, untrusted))
        return {"insights": insights, "the_one_thing": one or {}}

    monkeypatch.setattr(llm, "call_json_untrusted", answer)
    return seen


GOOD = {"headline": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
        "headline_facts": ["F1"],
        "reasoning": "Treasury officials were summoned by the committee, as two outlets reported.",
        "reasoning_facts": ["F1"],
        "implication": "Pressure on the disbursements is institutional, not only political."}


def test_an_insight_on_the_record_stands_with_its_citations(monkeypatch):
    seen = _writer(monkeypatch, [GOOD])
    result = from_facts.insights_from_record(SUBJECT, RECORD)
    assert result["used"] is True
    insight = result["insights"][0]
    assert "[1]" in insight["headline"] and insight["headline_citations"]
    assert insight["basis"] == "verified_facts"
    assert insight["implication"].startswith("Pressure")
    assert "F1" in seen[0][1], "the writer was not handed the ledger"


def test_an_imagined_pattern_is_removed(monkeypatch):
    invented = {"headline": "A coordinated network of bot accounts amplified attacks on Wanjiru.",
                "headline_facts": ["F2"], "reasoning": "", "implication": ""}
    _writer(monkeypatch, [invented, GOOD])
    result = from_facts.insights_from_record(SUBJECT, RECORD)
    assert len(result["insights"]) == 1
    assert result["check"]["removed"][0]["field"] == "headline"


def test_the_strength_is_counted_not_claimed(monkeypatch):
    single = {"headline": "Jane Wanjiru said fourteen counties could not pay health workers.",
              "headline_facts": ["F2"], "confidence": "high"}
    alleged = {"headline": "Opposition senators alleged that the funds were diverted to a private account.",
               "headline_facts": ["F3"]}
    _writer(monkeypatch, [GOOD, single, alleged])
    kept = from_facts.insights_from_record(SUBJECT, RECORD)["insights"]
    strengths = [i["strength"] for i in kept]
    assert strengths == ["corroborated", "single", "alleged"], \
        "the writer's own 'high' must not outrank a single source"


def test_a_pattern_is_counted_strength(monkeypatch):
    ledger = from_facts.build_ledger(FINDINGS)
    ledger += from_facts.pattern_entries(RECORD["patterns"], ledger)
    p = next(e["id"] for e in ledger if e.get("pattern"))
    assert from_facts.strength([p], ledger) == "counted"


def test_an_implication_may_not_add_a_fact(monkeypatch):
    smuggled = dict(GOOD, implication="Expect Peter Kamau to resign by 2027.")
    _writer(monkeypatch, [smuggled])
    result = from_facts.insights_from_record(SUBJECT, RECORD)
    insight = result["insights"][0]
    assert "implication" not in insight, "the headline survives; the smuggled judgement does not"
    assert result["check"]["removed"][0]["kept_headline"] is True


def test_reasoning_that_says_more_than_its_findings_is_dropped_not_the_insight(monkeypatch):
    stretched = dict(GOOD, reasoning="Treasury officials were summoned after 2,300 protesters marched.")
    _writer(monkeypatch, [stretched])
    insight = from_facts.insights_from_record(SUBJECT, RECORD)["insights"][0]
    assert "reasoning" not in insight


def test_the_one_thing_is_checked_too(monkeypatch):
    _writer(monkeypatch, [GOOD], one={"text": "Wanjiru will be governor by 2027.", "facts": ["F2"]})
    result = from_facts.insights_from_record(SUBJECT, RECORD)
    assert result["the_one_thing"] == ""
    assert any(r["field"] == "the_one_thing" for r in result["check"]["removed"])


def test_a_thin_record_keeps_the_analysts_reading_and_says_why(monkeypatch):
    _writer(monkeypatch, [GOOD])
    result = from_facts.insights_from_record(SUBJECT, {"findings": FINDINGS[:1]})
    assert result["used"] is False and "finding" in result["reason"]


def test_nothing_surviving_keeps_the_analysts_reading(monkeypatch):
    _writer(monkeypatch, [{"headline": "Wanjiru will win in 2027.", "headline_facts": ["F2"]}])
    result = from_facts.insights_from_record(SUBJECT, RECORD)
    assert result["used"] is False and "survived" in result["reason"]


def test_the_page_shows_the_counted_strength_and_labels_the_judgement():
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    at = html.index("function renderInsights(")
    body = html[at:at + 4000]
    assert "ins.strength" in body, "the page fell back to the writer's own confidence"
    assert "Assessment" in body
    assert "op-only" in body, "the sentence check is the operator's, not the client's"


def test_the_framework_carries_the_one_things_citations():
    from engine.reports import sentiment_framework

    src = Path(sentiment_framework.__file__).read_text()
    assert '"the_one_thing_citations"' in src, \
        "written from the record it carries [n] markers, which render bare without the list"

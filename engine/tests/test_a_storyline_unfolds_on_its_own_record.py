"""A storyline's chronology is traced from its own findings, and checked.

The deep-dive analyst read a storyline's posts and wrote 250-500 words of
"how it unfolded" that nothing checked, and the Sentiment Framework then
printed that prose as the evidence under its most important issue — through
`esc()` alone, so the citation markers linkify had put in it reached the
reader as bare brackets.

Now each storyline gets its own record — the findings its own items
yielded, joined on the source ref, listed with no model writing anything —
and a chronology written from that record, each sentence passing the
brief's check and allowed to cite only findings that belong to its
storyline. The framework carries the citations with the prose.
"""

from pathlib import Path

from engine import llm
from engine.evidence import facts
from engine.reports import from_facts, sentiment_framework

SUBJECT = "Jane Wanjiru"


def _finding(statement, ref, day, status="reported", origins=1, publisher="Daily Nation", quote=None):
    return {"statement": statement, "status": status, "independent_sources": origins,
            "first_seen": day, "last_seen": day,
            "sources": [{"publisher": publisher, "url": f"https://{ref}.example", "ref": ref,
                         "published_at": day, "quote": quote or statement.lower().rstrip(".")}]}


SUMMONED = _finding("The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
                    "aaaa1111", "2026-10-14", origins=2)
APPEARED = _finding("Treasury officials appeared before the Senate Finance Committee on the disbursements.",
                    "bbbb2222", "2026-10-18")
ALLEGED = _finding("Opposition senators alleged that the funds were diverted to a private account.",
                   "cccc3333", "2026-10-16", status="alleged")
ELSEWHERE = _finding("Jane Wanjiru opened a maternity wing at the county referral hospital.",
                     "dddd4444", "2026-10-02")
FINDINGS = [SUMMONED, APPEARED, ALLEGED, ELSEWHERE]

STORYLINES = [
    {"label": "Disbursement standoff",
     "mention_ids": ["aaaa1111-0000-0000", "bbbb2222-0000-0000", "cccc3333-0000-0000"]},
    {"label": "Health openings", "mention_ids": ["dddd4444-0000-0000"]},
    {"label": "Nothing on the record", "mention_ids": ["eeee5555-0000-0000"]},
]


def test_a_storyline_record_is_joined_on_its_own_items_oldest_first():
    joined = facts.by_storyline(FINDINGS, STORYLINES)
    assert [f["statement"] for f in joined["Disbursement standoff"]] == [
        SUMMONED["statement"], ALLEGED["statement"], APPEARED["statement"]]
    assert [f["statement"] for f in joined["Health openings"]] == [ELSEWHERE["statement"]]
    assert "Nothing on the record" not in joined, "a storyline with no findings has no record, not an empty one"


def test_sounding_like_a_storyline_is_not_belonging_to_it():
    lookalike = _finding("The Treasury disbursement standoff with the Senate continued.", "ffff6666", "2026-10-20")
    joined = facts.by_storyline([lookalike], STORYLINES)
    assert joined == {}, "joined on wording rather than on the items the storyline holds"


def _writer(monkeypatch, storylines):
    calls = []

    def answer(instructions, untrusted, expected_keys, **kw):
        calls.append(untrusted)
        return {"storylines": storylines}

    monkeypatch.setattr(llm, "call_json_untrusted", answer)
    return calls


BY_STORY = facts.by_storyline(FINDINGS, STORYLINES)


def _ids_for(label):
    ledger = from_facts.build_ledger(
        [f for f in BY_STORY["Disbursement standoff"]] + BY_STORY["Health openings"], size=10)
    by_statement = {e["statement"]: e["id"] for e in ledger}
    return {f["statement"]: by_statement[f["statement"]] for f in BY_STORY[label]}


def test_a_chronology_on_the_record_replaces_the_analysts(monkeypatch):
    ids = _ids_for("Disbursement standoff")
    calls = _writer(monkeypatch, [{"label": "Disbursement standoff", "sentences": [
        {"text": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
         "facts": [ids[SUMMONED["statement"]]]},
        {"text": "Opposition senators alleged that the funds were diverted to a private account.",
         "facts": [ids[ALLEGED["statement"]]]},
        {"text": "Treasury officials then appeared before the Senate Finance Committee on the disbursements.",
         "facts": [ids[APPEARED["statement"]]]},
    ]}])
    result = from_facts.storylines_from_record(SUBJECT, BY_STORY)
    story = result["storylines"]["Disbursement standoff"]
    assert "[1]" in story["unfolded"] and story["unfolded_citations"]
    assert story["summary"] == {"findings": 3, "corroborated": 1, "alleged": 1,
                                "first_seen": "2026-10-14", "last_seen": "2026-10-18"}
    assert "STORYLINES" in calls[0] and "Disbursement standoff:" in calls[0]


def test_a_sentence_citing_another_storylines_finding_is_removed(monkeypatch):
    ids = _ids_for("Disbursement standoff")
    other = _ids_for("Health openings")[ELSEWHERE["statement"]]
    _writer(monkeypatch, [{"label": "Disbursement standoff", "sentences": [
        {"text": "Jane Wanjiru opened a maternity wing at the county referral hospital.", "facts": [other]},
        {"text": "The Senate Finance Committee summoned the Treasury over delayed county disbursements.",
         "facts": [ids[SUMMONED["statement"]]]},
    ]}])
    result = from_facts.storylines_from_record(SUBJECT, BY_STORY)
    removed = result["check"]["removed"]
    assert any("belong to no item in this storyline" in r["reason"] for r in removed)
    assert "unfolded" not in result["storylines"]["Disbursement standoff"], \
        "one surviving sentence is not a chronology; the analysts' stands"
    assert result["storylines"]["Disbursement standoff"]["record"], "the record is shown whatever the writer did"


def test_an_invented_development_is_removed(monkeypatch):
    ids = _ids_for("Disbursement standoff")
    _writer(monkeypatch, [{"label": "Disbursement standoff", "sentences": [
        {"text": "The Treasury Cabinet Secretary resigned after 2,300 protesters marched on Parliament.",
         "facts": [ids[APPEARED["statement"]]]},
    ]}])
    result = from_facts.storylines_from_record(SUBJECT, BY_STORY)
    assert result["check"]["removed"] and result["check"]["kept"] == 0


def test_a_reworded_label_belongs_to_no_storyline(monkeypatch):
    _writer(monkeypatch, [{"label": "The disbursement fight", "sentences": [
        {"text": "Anything at all.", "facts": ["F1"]}]}])
    result = from_facts.storylines_from_record(SUBJECT, BY_STORY)
    assert result["check"]["removed"][0]["reason"] == "not a storyline on the record"


def test_the_record_alone_needs_no_model(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the record was written by a model")

    monkeypatch.setattr(llm, "call_json_untrusted", refuse)
    result = from_facts.storylines_from_record(SUBJECT, BY_STORY, write_model=False)
    assert set(result["storylines"]) == {"Disbursement standoff", "Health openings"}
    assert result["storylines"]["Health openings"]["record"][0]["sources"][0]["quote"]


def test_the_framework_carries_the_citations_with_the_prose():
    payload = {"narrative_deep_dives": [{
        "label": "Disbursement standoff", "how_it_unfolded": "Summoned [1].",
        "how_it_unfolded_citations": [{"n": 1, "ref": "aaaa1111", "url": "https://a.example"}],
        "supporter_framing": "Support [1].", "supporter_framing_citations": [{"n": 1, "ref": "x", "url": "u"}],
        "unfolded_basis": "verified_facts", "record_summary": {"findings": 3}}]}
    evidence = sentiment_framework._evidence_for("Disbursement standoff", payload)
    assert evidence["how_it_unfolded_citations"][0]["ref"] == "aaaa1111"
    assert evidence["supporter_framing_citations"]
    assert evidence["unfolded_basis"] == "verified_facts"


def _page():
    return (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()


def test_the_framework_box_links_its_markers_rather_than_printing_them():
    html = _page()
    at = html.index("function fwEvidence(")
    body = html[at:at + 2500]
    assert "ev.how_it_unfolded_citations" in body
    assert "ev.supporter_framing_citations" in body and "ev.critic_framing_citations" in body


def test_the_storyline_card_shows_its_record_and_keeps_the_check_for_the_operator():
    html = _page()
    at = html.index("function renderNarrativeDeepDives(")
    body = html[at:at + 5000]
    assert "recordRow(" in body, "the storyline's record must read like the main record"
    assert "op-only" in body
    assert "unfolded_basis" in body

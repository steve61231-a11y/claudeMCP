"""The Issue Map's answer is written from the record, and checked with no model.

The map opened with a verdict a model wrote over a digest, unchecked — the
sentence a client is most likely to repeat, and in a client's file "X is
tied to the IMF loan" is an allegation unless something on the record
says it. Phase 3 of docs/ISSUE_MAP_FOUNDATION.md: the writer gets a ledger
of verified findings naming both (F) and roles a public record states (R),
every sentence must cite them, and `from_facts.check` removes any that
says more than they do. Too little on the record, and the analysts' verdict
stays — labelled as their reading.
"""

from datetime import datetime
from pathlib import Path

from engine import llm
from engine.evidence import paths
from engine.ingestion import kenya_law_records
from engine.reports import issue_evidence_status, verdict_from_record


def _finding(statement, status="reported", origins=2):
    return {"statement": statement, "status": status, "independent_sources": origins, "first_seen": "2026-10-02",
            "sources": [{"publisher": "Daily Nation", "ref": "aaaa1111", "url": "https://n.example/a",
                         "quote": statement.lower()}]}


RECORD = {"state": "measured", "findings": [
    _finding("The National Treasury announced the sale of 15 per cent of Safaricom shares to Vodacom."),
    _finding("Activists alleged the Safaricom shares sale undervalued the stake.", status="alleged")]}


def _paths():
    title = ("Gachoka & 3 others v Cabinet Secretary, National Treasury & Economic Planning & 11 others "
             "(Petition E051 of 2026) [2026] KEHC 8409 (KLR)")
    card = {"title": title, "url": "https://records.example/e051", "kind": "judgment", "court": "High Court",
            "court_code": "kehc", "date": datetime(2026, 9, 15), "parties": kenya_law_records.parties(title),
            "case_number": kenya_law_records.case_number(title), "snippet": "sale of Safaricom shares"}
    return paths.build([kenya_law_records.as_mention(card)], "Kenya Government", ["Kenya Government"],
                       "Safaricom shares sale")


def test_a_thin_record_leaves_the_analysts_verdict_and_says_why():
    out = verdict_from_record.verdict("Kenya Government", "Safaricom shares sale",
                                      {"state": "measured", "findings": []}, {"state": "measured", "paths": []})
    assert out["used"] is False and "0 entries on the record" in out["reason"]


def test_only_what_the_record_says_survives(monkeypatch):
    def fake(prompt, data, **kw):
        assert "R1 [PUBLIC RECORD" in data and "F2 [ALLEGED" in data
        return {"sentences": [
            {"text": "The National Treasury announced the sale of 15 per cent of Safaricom shares to Vodacom.",
             "facts": ["F1"]},
            {"text": "Kenya Government was named as respondent in Petition E051 of 2026 at the High Court.",
             "facts": ["R1"]},
            {"text": "Kenya Government corruptly sold Safaricom to cronies.", "facts": ["R1"]},
            {"text": "The Safaricom shares sale undervalued the stake.", "facts": ["F2"]},
        ]}

    monkeypatch.setattr(llm, "call_json_untrusted", fake)
    out = verdict_from_record.verdict("Kenya Government", "Safaricom shares sale", RECORD, _paths())
    assert out["used"] and out["check"]["kept"] == 2
    reasons = " | ".join(r["reason"] for r in out["check"]["removed"])
    assert "corruptly" in reasons or "says more" in reasons, "a respondent is not a wrongdoer"
    assert "only allege" in reasons, "an allegation stated as fact is removed"
    assert any(c["url"] == "https://records.example/e051" for c in out["citations"])


def test_the_grade_and_the_page_follow_which_verdict_leads():
    assert issue_evidence_status.verdict({"verdict_from_record": {"used": True, "check": {"kept": 2}}})["grade"] \
        == "record"
    assert issue_evidence_status.verdict({"verdict_from_record": {"used": False}})["grade"] == "analyst"
    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "linkifyRefs(esc(vr.text),vr.citations)" in page and "Our analysts' reading" in page

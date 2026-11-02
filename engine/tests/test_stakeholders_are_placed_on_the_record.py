"""Each stakeholder's involvement in the issue, on the record.

Phase 3 of docs/ISSUE_MAP_FOUNDATION.md. The map's stakeholders — for,
against, neutral — are the analysts' reading of the coverage and rendered
as though established. Each now carries the verified statements naming
both it and the issue; the position stays the analysts' and is labelled so,
because "for" or "against" from a sentence is a judgement no code here
makes. A record that could not be read says so, never "named in 0".
"""

from pathlib import Path

from engine.evidence import stakeholder_record as sr
from engine.reports import issue_evidence_status, issue_match


def test_a_person_is_named_by_full_name_or_surname_an_institution_only_whole():
    assert sr.names("Ruto told the rally the Safaricom sale would go ahead.", "William Ruto")
    assert not sr.names("Treasury officials met.", "National Treasury"), "an institution only by its full name"
    assert sr.names("The National Treasury defended the sale.", "National Treasury")
    assert sr.names("Mbadi spoke on the sale.", "John Mbadi")
    assert not sr.names("Oko spoke.", "Jane Oko"), "a surname under four letters is too ambiguous"


def _finding(statement, origins=1, status="reported"):
    return {"statement": statement, "status": status, "independent_sources": origins, "first_seen": "2026-10-02",
            "sources": [{"publisher": "Daily Nation", "url": "https://n.example/a", "quote": statement.lower()}]}


def _framework():
    return {"main_contours": {"positions": {
        "for": {"segments": {"government": [{"name": "National Treasury"}]}},
        "against": {"segments": {"civil_society": [{"name": "Okiya Omtatah"}]}},
        "neutral": {"segments": {"other": [{"name": "Capital Markets Authority"}]}}}}}


def test_each_stakeholder_carries_its_statements_and_the_absence_is_said():
    fw = _framework()
    findings = [_finding("The National Treasury announced the Safaricom shares sale.", origins=3),
                _finding("Omtatah filed a petition against the Safaricom shares sale.", status="alleged")]
    summary = sr.annotate(fw, findings)
    pos = fw["main_contours"]["positions"]
    treasury = pos["for"]["segments"]["government"][0]["record"]
    assert treasury["count"] == 1 and treasury["findings"][0]["independent_sources"] == 3
    assert pos["against"]["segments"]["civil_society"][0]["record"]["count"] == 1
    assert pos["neutral"]["segments"]["other"][0]["record"] == {"state": "measured", "count": 0, "findings": []}
    assert summary == {"state": "measured", "stakeholders": 3, "on_record": 2}
    grade = issue_evidence_status.stakeholders({"issue_framework": fw})
    assert grade["grade"] == "mixed" and "2 of 3 stakeholders" in grade["detail"]


def test_an_unread_record_is_not_zero_statements():
    fw = _framework()
    sr.annotate(fw, None, state="not_measured")
    assert fw["main_contours"]["positions"]["for"]["segments"]["government"][0]["record"] == {"state": "not_measured"}
    assert issue_evidence_status.stakeholders({"issue_framework": fw})["grade"] == "analyst"


def test_the_record_is_read_from_the_maps_own_sources_about_anyone(db_session):
    from engine.db.models import Fact, Politician, RawMention

    subject = Politician(name="Kenya Government", subject_type="organization")
    db_session.add(subject)
    db_session.flush()
    mention = RawMention(politician_id=subject.id, platform="news", source_type="article", author_handle="Nation",
                         text="The National Treasury announced the Safaricom shares sale to Vodacom.",
                         posted_at=__import__("datetime").datetime(2026, 10, 2), content_hash="sr1")
    db_session.add(mention)
    db_session.flush()
    for i, statement in enumerate(["The National Treasury announced the Safaricom shares sale to Vodacom.",
                                   "The National Treasury raised fuel levies."]):
        db_session.add(Fact(politician_id=subject.id, source_key=f"mention:{mention.id}", mention_id=mention.id,
                            kind="fact", status="reported", statement=statement, quote=statement.lower(),
                            statement_hash=f"h{i}"))
    db_session.flush()
    corpus = [{"kind": "mention", "id": mention.id, "text": mention.text, "source_url": "https://n.example/a"}]
    found = sr.issue_findings(db_session, subject, corpus, issue_match.of("Safaricom shares sale"))
    assert [f["statement"] for f in found] == ["The National Treasury announced the Safaricom shares sale to Vodacom."]


def test_the_page_shows_the_record_under_each_stakeholder():
    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "${stakeholderRecord(s.record)}" in page
    assert "Not named with the issue in a verified statement" in page

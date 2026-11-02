"""The connection is a path of evidence: typed steps the records state, no model.

docs/ISSUE_MAP_FOUNDATION.md section 2 and Phase 2. A step exists only
where a record states the role — who brought a case and who answered it
(the court card's title), who spoke (the Hansard), who borrows and who
carries a project out (the World Bank). A co-mention is never a step.
Where the principal is in court only through the office that stands for
it, the step says so; and when no record ties them, the lead says which
absence it is.
"""

from datetime import datetime

from engine.evidence import paths
from engine.ingestion import kenya_law_records


def _case(title, snippet="the Social Health Authority (SHA) failed to pay", date=datetime(2025, 7, 10)):
    record = {"title": title, "url": "https://new.kenyalaw.org/akn/ke/judgment/x", "kind": "judgment",
              "court": "High Court", "court_code": "kehc", "date": date,
              "parties": kenya_law_records.parties(title), "case_number": kenya_law_records.case_number(title),
              "snippet": snippet}
    return kenya_law_records.as_mention(record)


def _speech(speaker, text="The Social Health Authority has not paid facilities in my county."):
    return {"source_type": "hansard", "author_handle": speaker, "text": text, "posted_at": datetime(2026, 10, 21),
            "raw_payload": {"source": "hansard", "sitting": "2026-10-21", "business": "PETITIONS",
                            "url": "https://parliament.example/hansard.pdf"}}


def _project(agency, borrower="Republic of Kenya"):
    return {"source_type": "report", "text": "World Bank project P1: Kenya Health Systems", "posted_at":
            datetime(2024, 5, 2), "raw_payload": {"source": "world_bank_projects", "project_id": "P1",
                                                  "project": "Kenya Health Systems", "implementing_agency": agency,
                                                  "borrower": borrower, "url": "https://wb.example/P1"}}


def test_a_party_named_in_the_title_is_a_typed_sourced_step():
    case = _case("Abdille v Ministry of Health & 3 others (Petition E078 of 2025) [2025] KEELRC 2027 (KLR)")
    out = paths.build([case], "Ministry of Health", ["Ministry of Health"], "SHA")
    assert out["state"] == "measured" and out["counts"]["court"] == 1
    first, second = out["paths"][0]["steps"]
    assert first["relation"] == "named as respondent in" and first["to"] == "Petition E078 of 2025"
    assert first["as_printed"] == "Ministry of Health & 3 others" and first["basis"] == "named"
    assert second["relation"] == "concerns" and second["to"] == "SHA"
    assert first["record"]["url"] == "https://new.kenyalaw.org/akn/ke/judgment/x"
    assert out["lead"] == "On the record, Ministry of Health is a party to 1 court case on it."


def test_the_office_that_stands_for_the_principal_is_said_as_the_basis():
    case = _case("Gachoka & 3 others v Cabinet Secretary, National Treasury & Economic Planning & 11 others "
                 "(Petition E051 of 2026) [2026] KEHC 8409 (KLR)", snippet="sale of Safaricom shares")
    out = paths.build([case], "Kenya Government", ["Kenya Government", "Government of Kenya"],
                      "Safaricom shares sale")
    step = out["paths"][0]["steps"][0]
    assert step["basis"] == "the national government's officer" and step["relation"] == "named as respondent in"
    ministry = _case("Abdille v Cabinet Secretary for Health & another (Petition E1 of 2025) [2025] KEHC 1 (KLR)")
    out = paths.build([ministry], "Ministry of Health", ["Ministry of Health"], "SHA")
    assert out["paths"][0]["steps"][0]["basis"] == "the Ministry's Cabinet or Principal Secretary"


def test_a_name_in_the_snippet_is_not_a_role():
    """Two names in one record are the fact store's question; a step needs
    the record to state the role."""
    case = _case("Abdille v Social Health Authority (Petition E078 of 2025) [2025] KEELRC 2027 (KLR)",
                 snippet="the Ministry of Health and the Authority failed to pay")
    out = paths.build([case], "Ministry of Health", ["Ministry of Health"], "SHA")
    assert out["paths"] == [] and out["reach"]["court"] == 1
    assert out["lead"].startswith("No record read names Ministry of Health in a role on this issue: 1 court record")


def test_a_speaker_and_a_lender_role_are_steps_and_counted():
    records = [_speech("Hon. Jane Wanjiru (Nairobi County, UDA)"), _speech("Hon. John Mbadi (Suba South, ODM)"),
               _project("MINISTRY OF HEALTH")]
    out = paths.build(records, "Jane Wanjiru", ["Jane Wanjiru"], "SHA")
    assert out["counts"] == {"court": 0, "parliament": 1, "lender": 0, "rulings": 0}
    assert out["paths"][0]["steps"][0]["record"]["business"] == "PETITIONS"
    out = paths.build(records, "Ministry of Health", ["Ministry of Health"], "SHA")
    assert out["counts"]["lender"] == 1 and out["paths"][0]["steps"][0]["relation"] == "implementing agency of"


def test_which_absence_it_is():
    assert paths.build([], "X", ["X"], "SHA", sources={"skipped": "switched off"})["state"] == "not_measured"
    failed = {name: {"error": "down"} for name in ("kenya_law", "parliament", "world_bank")}
    assert paths.build([], "X", ["X"], "SHA", sources=failed)["state"] == "not_measured"
    nothing = paths.build([], "X", ["X"], "SHA", sources={"kenya_law": {"records": 0}})
    assert nothing["state"] == "measured" and nothing["lead"].startswith("No court record")


def test_the_next_dated_step_on_the_issue_travels_with_the_paths():
    ahead = [{"date": "2026-11-12", "event": "Second Reading — The Social Health Authority (Amendment) Bill",
              "publisher": "Parliament of Kenya — Order Paper", "url": "https://parliament.example/op.pdf"}]
    out = paths.build([], "X", ["X"], "SHA", ahead=ahead)
    assert out["next"][0]["date"] == "2026-11-12"


def test_records_never_read_are_not_read_as_no_record():
    from engine.reports import issue_map

    out = issue_map._connection_paths([], "X", "SHA", {"stored": False, "reason": "database unavailable"})
    assert out["state"] == "not_measured" and "database unavailable" in out["reason"]


def test_the_map_carries_the_path_and_never_stores_the_raw_records():
    from pathlib import Path

    from engine.reports import issue_map

    src = Path(issue_map.__file__).read_text()
    assert 'record_items = acquisition.pop("record_items", None)' in src, "in memory only, popped at once"
    assert 'payload["connection_paths"] = _connection_paths(' in src
    assert 'out["connection_paths"] = _connection_paths(' in src, "a thin map may still have a record tying them"
    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "renderConnectionPaths(m.connection_paths,m)" in page


def test_one_case_is_one_step_however_many_rulings_it_has():
    """A live map said "a party to 4 court cases" over four rulings in one
    consolidated petition."""
    rulings = [_case(t, snippet="sale of Safaricom shares", date=d) for t, d in [
        ("Gachoka & 3 others v Cabinet Secretary, National Treasury & Economic Planning & 11 others (Petition E051 "
         "of 2026 & E836 & E831 of 2025 (Consolidated)) [2026] KEHC 8409 (KLR)", datetime(2026, 6, 9)),
        ("Gachoka & 3 others v Cabinet Secretary, National Treasury and Economic Planning & 11 others (Petition "
         "E051, E836 & E831 of 2026 (Consolidated)) [2026] KEHC 13493 (KLR)", datetime(2026, 9, 15))]]
    out = paths.build(rulings, "Kenya Government", ["Kenya Government"], "Safaricom shares sale")
    assert out["counts"]["court"] == 1 and out["counts"]["rulings"] == 2
    step = out["paths"][0]
    assert step["steps"][0]["to"] == "Petition E051 of 2026"
    assert [r["citation"] for r in step["rulings"]] == ["[2026] KEHC 13493 (KLR)", "[2026] KEHC 8409 (KLR)"]
    assert out["lead"] == "On the record, Kenya Government is a party to 1 court case on it (2 rulings)."

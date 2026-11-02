"""Every Issue Map section says what it rests on.

Phase 3 of docs/ISSUE_MAP_FOUNDATION.md. The map's sections rendered alike
whether the record wrote them (the connection, the path), arithmetic
counted them (the data overview), or a model wrote them with nothing
checking (the verdict, the background, who stands where) — so a client
could not tell which sentences to repeat in a meeting. Each grade comes
from how the payload says the section was made, never from a model's view
of itself, and uses the Search report's vocabulary so a reader learns one.
"""

from pathlib import Path

from engine.reports import issue_evidence_status as es


def test_the_record_sections_are_graded_on_what_was_found():
    payload = {"connection_record": {"state": "measured", "counts": {"findings": 4, "corroborated": 2}},
               "connection_paths": {"state": "measured", "paths_total": 0, "reach": {"court": 3}}}
    out = es.assess(payload)
    assert out["connection_record"]["grade"] == "record"
    assert out["connection_paths"]["grade"] == "thin"
    assert "3 records on the issue read; none names them in a role" in out["connection_paths"]["detail"]


def test_what_could_not_be_read_is_the_operators_never_an_empty_finding():
    out = es.assess({"connection_record": {"state": "not_measured", "reason": "sources not stored"}})
    assert out["connection_record"]["grade"] == "unavailable"
    assert out["connection_paths"]["grade"] == "unavailable"


def test_the_analysts_sections_say_so_and_the_counted_one_says_so():
    out = es.assess({})
    for key in ("verdict", "background", "stakeholders"):
        assert out[key]["grade"] == "analyst", key
    assert out["data_overview"]["grade"] == "counted"
    assert out["recommendations"]["grade"] == "analyst"
    assert es.assess({"scenarios": {"state": "modelled"}})["recommendations"]["grade"] == "mixed"


def test_sequencing_is_graded_on_how_many_dates_a_source_states():
    seq = lambda rows: {"issue_framework": {"sequencing": {"engagement_timeline": rows}}}  # noqa: E731
    assert es.sequencing(seq([{"source": "stated"}]))["grade"] == "record"
    assert es.sequencing(seq([{"source": "stated"}, {}]))["grade"] == "mixed"
    assert es.sequencing(seq([{}]))["grade"] == "analyst"
    assert es.sequencing(seq([]))["grade"] == "thin"


def test_the_map_carries_the_grades_and_the_page_stamps_them():
    src = Path(es.__file__).with_name("issue_map.py").read_text()
    assert 'payload["evidence_status"] = issue_evidence_status.assess(payload)' in src
    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    for key in ("background", "stakeholders", "recommendations", "sequencing", "data_overview"):
        assert f"fwStamp(s" in page and f"'{key}')" in page, key
    assert "stampEvidence(conn,ES.connection_record)" in page
    assert "stampEvidence(cpath,ES.connection_paths)" in page and "stampEvidence(head,ES.verdict)" in page


def test_the_data_overview_lists_the_checks_that_ran_never_one_that_did_not():
    """"Claim verification: not run" was printed on every map: that check is
    the Search report's and never runs on the map, while the map's own
    checks went unmentioned."""
    from engine.reports import issue_framework

    payload = {"verdict_from_record": {"used": True, "check": {"kept": 3, "written": 4}},
               "grounding": {"removed": [{"sentence": "x"}]}, "protected_terms": {"removed": []}}
    checks = issue_framework.build_data_overview(payload, [], [])["controls_applied"]
    assert "claim_verification" not in checks
    assert checks["answer_from_the_record"].startswith("3 of 4 sentence(s) kept")
    assert checks["names_checked_against_sources"] == "1 sentence(s) removed"
    assert checks["identity_guard"] == "0 sentence(s) removed"
    bare = issue_framework.build_data_overview({}, [], [])["controls_applied"]
    assert not {"answer_from_the_record", "names_checked_against_sources", "identity_guard"} & set(bare)

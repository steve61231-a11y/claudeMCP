"""Each section of the report says how much it can bear.

Phase 3's last promise (docs/FOUNDATION.md): "the fixed report … each section
stating whether its evidence is complete, thin or failed." A section written
from the verified record and one an analyst wrote with nothing checking it
rendered identically, so a client could not tell which sentences to repeat
in a meeting and which were one analyst's reading. The founding rule pointed
at trust: a section must never look stronger than its evidence.

The grade is computed from how the payload says the section was made, never
from a model's view of itself.
"""

from pathlib import Path

from engine.reports import evidence_status as es


def _f(n_sources=1):
    return {"statement": "x", "independent_sources": n_sources}


def test_the_record_is_graded_by_how_much_is_on_it():
    assert es.established_record({})["grade"] == "unavailable"
    assert es.established_record({"established_record": {"findings": [], "findings_total": 0}})["grade"] == "thin"
    assert es.established_record({"established_record": {"findings": [_f(), _f()], "findings_total": 2}})["grade"] == "thin"
    strong = es.established_record({"established_record": {"findings": [_f(3), _f(), _f(2)], "findings_total": 7}})
    assert strong == {"grade": "record", "label": "On the record",
                      "detail": "7 findings, 2 independently corroborated"}


def test_a_brief_the_record_did_not_write_says_it_is_the_analysts():
    assert es.executive_brief({})["grade"] == "analyst"
    checked = es.executive_brief({"brief_basis": "verified_facts",
                                  "brief_check": {"check": {"removed": [{"sentence": "s"}]}}})
    assert checked["grade"] == "record" and "1 sentence the check could not support removed" in checked["detail"]


def test_a_timeline_part_on_the_record_says_which_part():
    payload = {"timeline": [{"date": "2026-10-01", "basis": "verified_facts"},
                            {"date": "2026-10-03"}, {"date": "2020-01-01", "before_period": True}]}
    assert es.timeline(payload) == {"grade": "mixed", "label": "Partly on the record",
                                    "detail": "1 of 2 days on the record; the rest are analyst briefings"}


def test_quotes_are_counted_and_an_unquoted_reading_is_not_dressed_as_checked():
    voice = {"public_voice": {"critical": [{"theme": "t", "quotes": [{"ref": "a"}, {"ref": "b"}]},
                                           {"theme": "u", "quotes": []}]}}
    got = es.public_voice(voice)
    assert got["grade"] == "quoted" and "2 quotes found in their sources, 1 of 2 without one" in got["detail"]
    bare = es.platform_pulse({"platform_pulse": [{"platform": "x", "quotes": []}]})
    assert bare["grade"] == "analyst"


def test_search_demand_is_counted_and_its_absence_is_the_operators():
    assert es.search_demand({"search_demand": {"state": "measured"}})["grade"] == "counted"
    assert es.search_demand({"search_demand": {"state": "too_little_search"}})["grade"] == "thin"
    assert es.search_demand({"search_demand": {"state": "not_measured", "reason": "no key"}}) == \
        {"grade": "unavailable", "label": "Not available this run", "detail": "no key"}


def test_every_section_is_assessed_and_a_failure_to_assess_is_said():
    out = es.assess({})
    assert set(out) == set(es.SECTIONS)

    class Broken(dict):
        def get(self, *a, **k):
            raise RuntimeError("unreadable")

    broken = es.assess(Broken())
    assert all(v["label"] == "Not assessed" for v in broken.values())


def test_the_api_carries_it_and_the_page_stamps_every_section():
    html = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    for key in es.SECTIONS:
        if key in ("executive_brief", "risks"):
            continue
        assert f"'{key}')" in html, f"the {key} card is not stamped"
    assert "stampEvidence(b,(r.evidenceStatus||{}).executive_brief)" in html
    assert "(r.evidenceStatus||{}).risks" in html
    stamp = html[html.index("function stampEvidence("):]
    assert "op-only" in stamp[:900], "an unavailable section is the operator's to see"
    src = (Path(__file__).resolve().parents[1] / "api_server.py").read_text()
    assert '"evidenceStatus": _evidence_status(payload)' in src

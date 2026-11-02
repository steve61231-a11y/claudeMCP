"""What changed since the last map of the same pair — counted, no model.

Phase 5 of docs/ISSUE_MAP_FOUNDATION.md. The corpus compounds and the
records are re-read every time, so a client mapping the same pair again
wants the difference first: a new case or ruling, new verified statements,
a hearing that appeared or moved, a stakeholder newly on the record. Every
finished map is stored; the previous one is read before this one is
stored. A first map says so — nothing is compared against nothing — and
what left is counted, never hidden.
"""

from pathlib import Path

from engine.reports import map_changes


def _map(cases=(), statements=(), steps=(), on_record=(), generated="2026-10-01T09:00:00"):
    return {"generated_at": generated,
            "connection_paths": {"state": "measured", "paths": [
                {"kind": "court", "case": key, "rulings": [{}] * n,
                 "steps": [{"to": key.split(" (")[0], "relation": "named as respondent in",
                            "record": {"url": f"https://records.example/{i}"}}]}
                for i, (key, n) in enumerate(cases)], "next": list(steps)},
            "connection_record": {"findings": [{"statement": s, "status": "reported",
                                                "sources": [{"url": "https://n.example", "publisher": "Nation"}]}
                                               for s in statements]},
            "issue_framework": {"main_contours": {"positions": {"for": {"segments": {"g": [
                {"name": n, "record": {"count": 1}} for n in on_record]}}}}}}


def test_a_first_map_says_it_is_the_first():
    assert map_changes.compare(None, _map()) == {"state": "first_map"}


def test_new_cases_rulings_statements_and_dates_are_counted_and_listed():
    before = _map(cases=[("Petition E051 of 2026 (kehc)", 2)], statements=["A said the sale was approved."],
                  steps=[{"date": "2099-01-12", "event": "Petition E051 of 2026 is listed before the High Court"}])
    now = _map(cases=[("Petition E051 of 2026 (kehc)", 4), ("Petition E9 of 2026 (kehc)", 1)],
               statements=["A said the sale was approved.", "The Treasury defended the sale."],
               steps=[{"date": "2099-02-20", "event": "Petition E051 of 2026 is listed before the High Court"},
                      {"date": "2099-03-01", "event": "Second Reading of the Bill"}],
               on_record=["National Treasury"])
    out = map_changes.compare(before, now)
    assert out["state"] == "compared" and out["previous_generated"] == "2026-10-01T09:00:00"
    k = out["counts"]
    assert (k["new_cases"], k["new_rulings"], k["new_statements"], k["new_dated_steps"], k["moved_dates"],
            k["stakeholders_newly_on_record"]) == (1, 1, 1, 1, 1, 1)
    assert out["moved_dates"][0]["was"] == "2099-01-12" and out["moved_dates"][0]["date"] == "2099-02-20"
    assert out["new_rulings"][0]["rulings"] == 4 and out["new_rulings"][0]["before"] == 2
    assert out["lead"].startswith("Since the last map: 1 new court case, 1 case with a new ruling")


def test_nothing_new_is_said_and_what_left_is_counted():
    before = _map(cases=[("Petition E1 of 2025 (kehc)", 1)], statements=["X happened."])
    out = map_changes.compare(before, _map())
    assert out["lead"] == "Nothing new on the record since the last map of this pair."
    assert out["counts"]["cases_no_longer_found"] == 1 and out["counts"]["statements_no_longer_found"] == 1


def test_the_previous_map_is_read_before_this_one_is_stored_and_the_page_shows_it():
    root = Path(__file__).resolve().parents[2]
    src = (root / "engine" / "api_server.py").read_text()
    compare_at = src.index('payload["changes"] = map_changes.compare(_lookup_issue_map(principal, issue), payload)')
    assert compare_at < src.index("        _store_issue_map(principal, issue, payload)\n", compare_at)
    page = (root / "web" / "pulse_app.html").read_text()
    assert "renderMapChanges(m.changes)" in page and "if(!ch||ch.state==='first_map')return null;" in page

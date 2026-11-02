"""Every Issue Map carries its own quality record.

The Search report has been measured on every run since Phase 0 of
docs/FOUNDATION.md; the map never was, so a map that read sixteen documents,
all of them YouTube, looked no different from one that read the country's
newsrooms, and no change to the map could be shown to be better. Phase 0 of
docs/ISSUE_MAP_FOUNDATION.md: what was read, what the page rests on, what
the guards removed, what the paid sources cost — no model involved.
"""

from datetime import datetime
from pathlib import Path

from engine.reports import issue_quality


def _item(platform, source_type="article", pool="core", **k):
    return {"platform": platform, "source_type": source_type, "author_handle": platform,
            "text": "x", "posted_at": datetime(2026, 10, 1), "date_estimated": False,
            "evidence_pool": pool, "source_url": f"https://{platform}/a", **k}


THIN = [_item("youtube", "video") for _ in range(4)] + [_item("youtube", "comment", "principal_side")] * 2

PAYLOAD = {
    "intersection": {"verdict": "Our analysis indicates that the sale failed. The courts stopped it twice.",
                     "involvement": "The government sold the shares. It appealed and lost.",
                     "tension_or_risk": "", "sub_issues": [{"detail": "The value of the deal is disputed."}]},
    "connection_record": {"state": "measured", "findings_total": 2, "principal_findings": 5,
                          "lead": "2 verified statements on the record name both.",
                          "findings": [{"statement": "a"}, {"statement": "b"}]},
    "scenarios": {"state": "modelled",
                  "drivers": [{"reading": "The High Court nullified the sale of the shares."}],
                  "scenarios": [{"factors": [{"text": "The appeal was rejected by the court of appeal."}]}],
                  "recommendations": [{"rationale": "The High Court nullified the sale of the shares."}],
                  "check": {"removed": [{"sentence": "x"}]}},
    "grounding": {"removed": 1},
    "protected_terms": {"removed": 0},
    "acquisition": {"social_tier": {"credits_spent": 42.0, "credit_budget": 300.0},
                    "relevance_filter": {"examined": 16, "kept": 6, "dropped": 10}},
}


def test_a_thin_video_corpus_shows_as_one():
    q = issue_quality.assess(PAYLOAD, THIN)
    assert q["measured"] and q["kind"] == "issue_map"
    assert q["read"]["video_share"] == 100.0 and q["read"]["kenyan_outlets"] == 0
    assert "100% video" in q["headline"]
    assert q["relevance"]["examined"] == 16 and q["spend"]["socialcrawl_credits"] == 42.0


def test_the_page_is_split_by_what_it_rests_on():
    page = issue_quality.page_support(PAYLOAD)
    # lead + 2 findings + driver + factor + recommendation = 6 on the record;
    # verdict 2 + involvement 2 + sub-issue 1 = 5 unchecked.
    assert page == {"on_record": 6, "unchecked": 5, "record_share": 54.5}


def test_what_the_guards_removed_is_counted_whatever_shape_it_was_stored_in():
    assert issue_quality.removed(PAYLOAD) == {"grounding": 1, "identity": 0, "scenario_check": 1,
                                              "press_set_aside": 0}


def test_a_measurement_that_fails_says_so():
    class Broken(dict):
        def get(self, *a, **k):
            raise RuntimeError("unreadable")

    q = issue_quality.assess(Broken(), [])
    assert q["measured"] is False and "could not be measured" in q["error"]


def test_every_map_is_measured_and_listed():
    root = Path(__file__).resolve().parents[2]
    src = (root / "engine" / "reports" / "issue_map.py").read_text()
    assert src.count("issue_quality.assess(") == 2, "the full map and the nothing-on-topic map"
    assert '"protected_terms": identity_report' in src
    api = (root / "engine" / "api_server.py").read_text()
    assert '"kind": "issue_map" if str(period or "").startswith("issue:")' in api
    assert "q.kind==='issue_map'" in (root / "web" / "pulse_app.html").read_text()

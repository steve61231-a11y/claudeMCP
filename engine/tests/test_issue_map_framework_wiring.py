"""The issue map must actually hand the client framework to the caller.

The framework builder was correct in isolation but unreachable — nothing called
it — so the report the client was promised never reached the API. These tests
pin the wiring: actors become stakeholders, dated moments become the background
record, an unstated stance stays neutral, and a broken framework never takes the
issue map down with it.
"""

from datetime import datetime, timedelta

import pytest

from engine.reports import issue_map


@pytest.fixture()
def stub_analysis(monkeypatch):
    analysis = {
        "involvement": "Architect of the levy.",
        "linking_narratives": [],
        "key_actors": [
            {"name": "National Treasury", "relation": "sponsors the bill",
             "entity_type": "organization", "position": "for", "influence": 90},
            {"name": "Consumer Watchdog", "relation": "opposes it",
             "entity_type": "organization", "position": "against", "influence": 40},
            {"name": "Unclear Actor", "relation": "mentioned once"},
        ],
        "timeline": [
            {"when": "last year", "date": (datetime.utcnow() - timedelta(days=200)).date().isoformat(),
             "event": "Bill tabled"},
            {"when": "long ago", "date": None, "event": "Undated moment"},
        ],
        "tension_or_risk": "", "verdict": "",
    }
    monkeypatch.setattr("engine.reports.analysts.analyze_issue_intersection",
                        lambda *a, **k: analysis)
    monkeypatch.setattr("engine.reports.digest.build_corpus_digest",
                        lambda label, mentions: {"coverage": {"mentions_total": len(mentions)}})
    return analysis


# A corpus, not an empty list. build_issue_map now stops before the digest when
# there is nothing to analyse, which is correct production behaviour — these
# tests exercise the wiring downstream of that, so they must supply material.
_CORPUS = [
    {"id": f"m{i}", "platform": "nation.africa", "source_type": "article",
     "author_handle": "nation", "text": f"Treasury CS on the Digital Services Tax, item {i}.",
     "posted_at": datetime.utcnow() - timedelta(days=i), "engagement": {},
     "source_url": f"https://nation.africa/{i}"}
    for i in range(6)
]


def _build(**kwargs):
    return issue_map.build_issue_map("Treasury CS", "Digital Services Tax",
                                     mentions=list(_CORPUS), **kwargs)


def test_framework_is_attached_to_the_issue_map(stub_analysis):
    payload = _build()
    fw = payload["issue_framework"]

    assert fw["framework"] == "Issue Analysis & Mapping Framework V1.0"
    assert fw["input_1_issue_definition"]["dependent_variable"] == "Digital Services Tax"


def test_key_actors_become_positioned_stakeholders(stub_analysis):
    fw = _build()["issue_framework"]
    positions = fw["main_contours"]["positions"]

    assert [s["name"] for s in positions["for"]["segments"]["public"]] == ["National Treasury"]
    assert positions["against"]["total_identified"] == 1


def test_an_unstated_stance_is_neutral_not_invented(stub_analysis):
    fw = _build()["issue_framework"]
    neutrals = [s["name"] for s in fw["stakeholder_networks"]["neutral"]]
    assert "Unclear Actor" in neutrals


def test_only_dated_moments_reach_the_timeline(stub_analysis):
    fw = _build()["issue_framework"]
    events = [e["event"] for e in fw["background_and_context"]["timeline_of_major_developments"]]

    assert events == ["Bill tabled"]  # the undated moment carries no fabricated date


def test_desired_outcome_flows_through_to_input_2(stub_analysis):
    fw = _build(desired_outcome="A softer DST regime")["issue_framework"]
    assert fw["input_2_desired_outcome"]["provided"] is True

    without = _build()["issue_framework"]
    assert without["input_2_desired_outcome"]["provided"] is False


def test_framework_failure_does_not_lose_the_issue_map(stub_analysis, monkeypatch):
    monkeypatch.setattr("engine.reports.issue_framework.build",
                        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    payload = _build()

    assert payload["issue_framework"] is None
    assert payload["intersection"]["involvement"] == "Architect of the levy."

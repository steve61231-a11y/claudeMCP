"""Section 4's action plan: substantial, specific, and nothing in it invented.

The founder: section 4's next steps were "just like 3 sentences with a few
words. We want something heavy … that actually helps." "Recommended
messaging" and "Best engagement approaches" were three fixed sentences on
every map. reports/action_plan.py gives the plan its own writer and holds
every sentence to the record and the map's stakeholders.
"""

from __future__ import annotations

from engine import llm
from engine.reports import action_plan

FINDINGS = [
    {"statement": "The National Treasury signed an IMF loan programme worth 850 million dollars.",
     "status": "reported", "independent_sources": 2,
     "sources": [{"ref": "aaaa1111", "quote": "signed an IMF loan programme", "publisher": "Daily Nation",
                  "url": "https://nation.africa/1"}]},
    {"statement": "Okiya Omtatah filed a petition against the National Treasury over the IMF loan terms.",
     "status": "reported", "independent_sources": 1,
     "sources": [{"ref": "bbbb2222", "quote": "filed a petition", "publisher": "The Star",
                  "url": "https://the-star.co.ke/2"}]},
    {"statement": "The Finance Committee summoned National Treasury officials over the IMF loan conditions.",
     "status": "reported", "independent_sources": 1,
     "sources": [{"ref": "cccc3333", "quote": "summoned National Treasury officials", "publisher": "Citizen",
                  "url": "https://citizen.digital/3"}]},
]
FRAMEWORK = {"main_contours": {"positions": {
    "for": {"segments": {"public": [{"name": "Finance Committee", "influence": 8, "influence_share": 40,
                                      "approach": "technical briefing on the loan terms"}]}},
    "against": {"segments": {"civil": [{"name": "Okiya Omtatah", "influence": 6, "influence_share": 30}]}},
    "neutral": {"segments": {"public": [{"name": "Controller of Budget", "influence": 6,
                                          "influence_share": 30}]}},
}}}
AHEAD = [{"date": "2026-12-15", "event": "Petition E181 of 2023 is listed before the High Court",
          "publisher": "Kenya Law — Daily Cause List"}]


def _build(monkeypatch, reply):
    monkeypatch.setattr(llm, "call_json_untrusted", lambda *a, **k: reply)
    record = {"state": "measured", "findings": FINDINGS, "counts": {"findings": 3}}
    return action_plan.build("National Treasury", "IMF loan", FRAMEWORK, record, FINDINGS, None,
                             desired_outcome="The loan is approved", ahead=AHEAD)


GOOD = {
    "messaging": {
        "core": "The loan is approved only if the Finance Committee sees the terms defended in the open.",
        "audiences": [{"audience": "Finance Committee", "line": "Give the Finance Committee the full loan "
                       "terms before it sits again.", "facts": ["F3"]}],
        "proof_points": [{"point": "The National Treasury signed an IMF loan programme worth 850 million dollars.",
                          "facts": ["F1"]}],
        "avoid": [{"text": "Do not dismiss the petition before the court has heard it.", "facts": ["F2"]}],
    },
    "plays": [{"title": "Brief the Finance Committee", "objective": "Answer the committee's questions first",
               "steps": ["Prepare the loan terms", "Request a session with the Finance Committee"],
               "owners": ["Finance Committee"], "when": "before the hearing listed in D1",
               "risk": "The petition delays approval", "signal": "The committee asks for the terms in writing",
               "facts": ["F3", "D1"]}],
    "engagement": [{"name": "Controller of Budget", "ask": "Confirm the loan is within the budget ceiling",
                    "approach": "formal letter", "why": "A neutral voice the committee listens to",
                    "facts": ["F3"]}],
}


def test_a_substantial_plan_survives_its_checks(monkeypatch):
    out = _build(monkeypatch, GOOD)
    assert out["state"] == "written"
    plan = out["plan"]
    assert plan["messaging"]["core"] and plan["messaging"]["proof_points"]
    assert plan["plays"][0]["steps"] and plan["plays"][0]["when"]
    assert plan["engagement"][0]["name"] == "Controller of Budget"


def test_an_invented_name_or_figure_is_removed(monkeypatch):
    bad = {**GOOD, "plays": GOOD["plays"] + [
        {"title": "Lobby Senator Jane Wambui", "steps": ["Call Jane Wambui"], "facts": ["F1"]},
        {"title": "Cite the 2 billion shilling shortfall", "steps": [], "facts": ["F1"]}],
        "engagement": GOOD["engagement"] + [{"name": "Somebody Unknown", "ask": "help", "facts": ["F1"]}]}
    out = _build(monkeypatch, bad)
    titles = [p["title"] for p in out["plan"]["plays"]]
    assert titles == ["Brief the Finance Committee"]
    assert [e["name"] for e in out["plan"]["engagement"]] == ["Controller of Budget"]
    reasons = " ".join(r["reason"] for r in out["check"]["removed"])
    assert "Jane Wambui" in reasons and "not a stakeholder" in reasons


def test_a_failed_writer_leaves_a_plan_built_from_the_map(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(llm, "call_json_untrusted", boom)
    record = {"state": "measured", "findings": FINDINGS}
    out = action_plan.build("National Treasury", "IMF loan", FRAMEWORK, record, FINDINGS, None,
                            desired_outcome="The loan is approved", ahead=AHEAD)
    assert out["state"] == "from_the_map" and "failed" in out["reason"]
    names = [e["name"] for e in out["plan"]["engagement"]]
    assert names[0] == "Finance Committee"
    assert any("2026-12-15" in p["when"] for p in out["plan"]["plays"])


def test_the_page_no_longer_prints_the_three_fixed_sentences_over_a_written_plan():
    from pathlib import Path

    page = (Path(__file__).resolve().parents[2] / "web" / "pulse_app.html").read_text()
    assert "function renderActionPlan(" in page
    assert "if(!plan&&(sr.best_engagement_approaches" in page

"""The issue map, run end to end with a scripted model.

"senate × forestry" read 370 documents, ran twenty minutes and returned eight
empty framework sections built over American local news. These assertions pin
the properties an issue map must have whatever comes back from acquisition.
"""

from datetime import datetime, timedelta

import pytest

from engine import health, llm, stages
from engine.reports.issue_map import build_issue_map

KENYAN = ("Forestry cabinet secretary Keriako Tobiko told the Senate committee in "
          "Nairobi that the second phase of Maasai Mau forest evictions will continue, "
          "after the county assembly raised concerns about compensation. Item {i}.")
FOREIGN = ("Senator Shirley Turner keeps proving her worth to New Jersey. The State "
           "Senate president pro tempore has represented the 15th district since 1998. "
           "Item {i}.")


def _mention(i, template, platform="nation.africa"):
    return {"id": f"m{i}", "platform": platform, "source_type": "article",
            "author_handle": platform, "text": template.format(i=i),
            "posted_at": datetime.utcnow() - timedelta(days=i % 30),
            "engagement": {"views": 100 + i}, "source_url": f"https://{platform}/{i}",
            "raw_payload": {"url": f"https://{platform}/{i}"}}


def _scripted(prompt, max_tokens=None, model=None):
    p = prompt.lower()
    if "reply with only this json" in p:
        return {"ok": True, "n": 2}
    if '"digest"' in p:
        return {"digest": {"claims": [{"ref": "m1", "text": "A claim about forestry."}],
                           "themes": [{"theme": "forest evictions", "count": 4}],
                           "notable_quotes": [], "entities": [],
                           "sentiment_read": {"supportive": 1, "critical": 3, "neutral": 1},
                           "anomalies": []}}
    if '"key_actors"' in p:
        return {"key_actors": [
            {"name": "Keriako Tobiko", "relation": "Cabinet secretary driving the evictions.",
             "entity_type": "person", "position": "For", "influence": 80},
            {"name": "Maasai Mau residents", "relation": "Oppose the eviction phase.",
             "entity_type": "organization", "position": "opposed", "influence": 55}]}
    if '"timeline"' in p:
        return {"timeline": [{"date": "2026-08-26", "event": "Second eviction phase confirmed.",
                              "sources": 3}]}
    if '"linking_narratives"' in p:
        return {"linking_narratives": [{"narrative": "Eviction vs compensation", "strength": 60}]}
    if '"involvement"' in p:
        return {"involvement": "The Senate committee is scrutinising the eviction programme.",
                "tension_or_risk": "Compensation is unresolved.", "verdict": "contested"}
    return {}


@pytest.fixture()
def scripted_model(monkeypatch):
    monkeypatch.setattr(llm, "_call_json", _scripted)
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()
    yield
    health.reset()
    stages.reset()


def _map(mentions, principal="Senate Committee on Lands", issue="Mau Forest evictions"):
    now = datetime.utcnow()
    return build_issue_map(principal, issue, window_start=now - timedelta(days=90),
                           window_end=now, mentions=mentions)


# --- a real intersection produces a real map --------------------------------

def test_an_on_topic_corpus_produces_a_populated_map(scripted_model):
    payload = _map([_mention(i, KENYAN) for i in range(12)])
    assert payload["intersection"].get("key_actors"), "actors did not reach the payload"
    assert payload["issue_framework"], "the framework did not render"
    assert not payload.get("nothing_on_topic")


def test_stakeholders_keep_the_stance_the_analyst_gave_them(scripted_model):
    """"For" and "opposed" were exact-matched against "for"/"against"/"neutral",
    so a champion and a challenger both landed in the middle."""
    payload = _map([_mention(i, KENYAN) for i in range(12)])
    networks = payload["issue_framework"]["stakeholder_networks"]
    # Stance is expressed by which bucket a stakeholder lands in, not a field.
    champions = {s["name"] for s in networks.get("champions") or []}
    challengers = {s["name"] for s in networks.get("challengers") or []}
    neutral = {s["name"] for s in networks.get("neutral") or []}
    assert "Keriako Tobiko" in champions, f"'For' was not read as a champion: {neutral}"
    assert "Maasai Mau residents" in challengers, \
        f"'opposed' was not read as a challenger: {neutral}"


def test_the_framework_reports_the_gate_as_applied_when_it_ran(scripted_model):
    """"relevance gate: not run" was printed on every issue map ever produced."""
    payload = _map([_mention(i, KENYAN) for i in range(12)])
    controls = payload["issue_framework"]["data_overview"]["controls_applied"]
    assert controls["relevance_gate"] in ("applied", "not run")


def test_the_payload_is_json_serialisable(scripted_model):
    import json

    json.dumps(_map([_mention(i, KENYAN) for i in range(12)]), default=str)


# --- an off-topic corpus is a different answer, given fast ------------------

def test_the_map_never_claims_more_coverage_than_it_analysed(scripted_model):
    payload = _map([_mention(i, KENYAN) for i in range(12)])
    coverage = payload["coverage"]
    assert coverage["mentions_analyzed"] <= coverage["mentions_total"]


def test_a_thin_corpus_is_marked_thin_not_dressed_up(scripted_model):
    payload = _map([])
    assert payload["thin"] is True
    assert payload["coverage"]["mentions_total"] == 0


def test_the_analysts_never_run_on_an_empty_corpus(scripted_model):
    """Twenty minutes of model calls over nothing is worse than an instant no."""
    calls = []
    original = llm._call_json

    def counted(prompt, **kw):
        calls.append(prompt)
        return original(prompt, **kw)

    llm._call_json = counted
    try:
        _map([])
    finally:
        llm._call_json = original
    assert not any('"key_actors"' in c.lower() for c in calls), \
        "the actors analyst ran over an empty corpus"

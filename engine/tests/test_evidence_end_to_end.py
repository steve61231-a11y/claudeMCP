"""The evidence pipeline run whole, with a scripted model.

independence -> records -> findings -> contradiction -> sceptic. The unit tests
cover each stage; this asserts the properties of the thing they produce
together, which is where every defect in this codebase has actually lived.
"""

from datetime import datetime, timedelta

import pytest

from engine import health, llm, stages
from engine.evidence.slice import run

WIRE = ("Nairobi Senator Edwin Sifuna told a rally in Kitale on Sunday that the "
        "coalition will field a single presidential candidate in 2027.")
DISTINCT = [
    "Traders in Gikomba say the new levy has cut their daily takings by a third.",
    "A parliamentary committee questioned the fuel levy's effect on transport costs.",
    "Matatu operators protested at City Hall over fare pressure and parking fees.",
    "Households in Nakuru report cutting meals as maize flour prices climb again.",
    "Economists warned the levy would feed straight into retail prices.",
]


def _corpus():
    now = datetime.utcnow()
    corpus = [{"id": f"w{i}", "text": WIRE, "platform": f"outlet{i}.co.ke",
               "author_handle": f"outlet{i}", "source_url": f"https://outlet{i}/x",
               "posted_at": now - timedelta(days=20 + i), "engagement": {}}
              for i in range(9)]
    corpus += [{"id": f"c{i}", "text": text, "platform": f"paper{i}.co.ke",
                "author_handle": f"paper{i}", "source_url": f"https://paper{i}/{i}",
                "posted_at": now - timedelta(days=i * 4), "engagement": {}}
               for i, text in enumerate(DISTINCT)]
    return corpus


def _scripted(prompt, max_tokens=None, model=None):
    p = prompt.lower()
    if "you are the sceptic" in p:
        if "coalition" in p:
            return {"verdict": "REVISE",
                    "reason": "Nine outlets, one wire story — not nine confirmations.",
                    "revised_title": "Single wire report of a 2027 pledge",
                    "what_would_disprove": "An outlet reporting it in its own words."}
        return {"verdict": "PASS", "reason": "Five independently written items.",
                "revised_title": "", "what_would_disprove": "Evidence the levy did not bite."}
    if "you are testing a claim" in p:
        return {"contradicting": [], "open_questions": []}
    records = []
    for block in prompt.split("Items:")[-1].split("\n\n"):
        block = block.strip()
        if not block.startswith("["):
            continue
        index = int(block.split("]")[0][1:])
        coalition = "coalition" in block.lower()
        records.append({
            "i": index,
            "kind": "event" if coalition else "fact",
            "status": "reported",
            "topic": "2027 coalition talks" if coalition else "cost of living",
            "statement": ("Sifuna told a Kitale rally the coalition will field one candidate."
                          if coalition else
                          "Traders and households report rising costs after the new levy."),
            "actor": "", "sentiment": "neutral" if coalition else "negative", "quote": "",
        })
    return {"records": records}


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


@pytest.fixture()
def result(scripted_model):
    return run("Edwin Sifuna", _corpus(), review=True, top_n=6)


# --- the central claim of the whole architecture ----------------------------

def test_nine_outlets_carrying_one_wire_story_count_as_one(result):
    assert result["independence"]["mentions"] == 14
    assert result["independence"]["distinct_stories"] == 6
    assert result["independence"]["largest_group"] == 9


def test_the_reposted_story_does_not_win_on_volume(result):
    """Ranking by mention count would put the most-reposted item on top by
    definition — the exact inversion this architecture exists to prevent."""
    titles = [f["title"] for f in result["findings"]]
    assert titles[0] == "cost of living", f"ranked wrong: {titles}"


def test_a_single_origin_is_never_high_confidence(result):
    wire = [f for f in result["findings"] if f["title"] == "2027 coalition talks"][0]
    assert wire["independent_sources"] == 1
    assert wire["mention_count"] == 9
    assert wire["confidence"] == "low"


def test_genuine_corroboration_is_high_confidence(result):
    cost = [f for f in result["findings"] if f["title"] == "cost of living"][0]
    assert cost["independent_sources"] == 5
    assert cost["confidence"] == "high"


# --- every claim must be checkable ------------------------------------------

def test_every_finding_carries_openable_evidence(result):
    for finding in result["findings"]:
        assert finding["supporting"], f"{finding['title']} has no evidence behind it"
        for row in finding["supporting"]:
            assert row["mention_id"], "a record with no provenance cannot be checked"


def test_every_evidence_row_traces_to_a_real_mention(result):
    ids = {m["id"] for m in _corpus()}
    for finding in result["findings"]:
        for row in finding["supporting"]:
            assert row["mention_id"] in ids, "a record cites a mention that does not exist"


def test_the_sceptic_reviewed_every_finding(result):
    for finding in result["findings"]:
        assert finding["review"].get("verdict") in ("PASS", "REVISE", "REJECT")


def test_the_result_is_json_serialisable(result):
    import json

    json.dumps(result, default=str)


# --- degradation stays honest ------------------------------------------------

def test_a_dead_model_yields_no_findings_rather_than_empty_ones(monkeypatch):
    monkeypatch.setattr(llm, "_call_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 404")))
    monkeypatch.setattr(llm, "provider", lambda: "openai_compatible")
    monkeypatch.setattr(llm, "bulk_model", lambda: "test/bulk")
    monkeypatch.setattr(llm, "strong_model", lambda: "test/strong")
    monkeypatch.setattr(llm, "concurrency", lambda n: 1)
    monkeypatch.setattr(llm, "max_output_tokens", lambda: 8000)
    health.reset()
    stages.reset()

    result = run("Edwin Sifuna", _corpus(), review=True)
    assert result["findings"] == []
    assert result["extraction"]["records"] == 0
    # The independence arithmetic needs no model, so it still stands.
    assert result["independence"]["distinct_stories"] == 6
    assert stages.current().failures, "a total extraction failure must be recorded"

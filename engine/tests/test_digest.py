from datetime import datetime

from engine.reports import digest as digest_mod
from engine.reports import analysts as analysts_mod


def _mentions(n):
    out = []
    for i in range(n):
        out.append({
            "id": f"m{i:04d}xxxx",
            "platform": "tiktok" if i % 2 else "news",
            "source_type": "comment" if i % 3 == 0 else "post",
            "author_handle": f"user{i}",
            "text": f"Mention number {i} saying something of moderate length about the subject " * 3,
            "posted_at": datetime(2026, 6, 1),
            "engagement": {"likes": i},
        })
    return out


def test_chunking_covers_every_mention():
    ms = _mentions(500)
    chunks = digest_mod._chunk_mentions(ms, budget=8000)
    # Union of chunks == the whole corpus, no drops, no duplicates.
    ids_in_chunks = [m["id"] for c in chunks for m in c]
    assert len(ids_in_chunks) == 500
    assert set(ids_in_chunks) == {m["id"] for m in ms}
    assert len(chunks) > 1  # actually partitioned


def test_build_corpus_digest_reports_full_coverage(monkeypatch):
    # Fake the LLM so the map step is deterministic and offline.
    monkeypatch.setattr(
        digest_mod.llm, "call_json",
        lambda *a, **k: {"digest": {"themes": [{"theme": "budget", "count": 3}], "claims": []}},
    )
    ms = _mentions(320)
    result = digest_mod.build_corpus_digest("Subject", ms)
    cov = result["coverage"]
    assert cov["mentions_total"] == 320
    assert cov["mentions_analyzed"] == 320  # EVERY mention passed through a chunk
    assert cov["complete"] is True
    assert cov["chunks"] == len(result["digests"])


def test_deep_insights_grounded_and_safe(monkeypatch):
    monkeypatch.setattr(
        analysts_mod.llm, "call_json",
        lambda *a, **k: {"insights": [{"headline": "Hidden coordination", "reasoning": "many near-identical posts",
                                        "confidence": "medium", "implication": "amplification"}],
                         "the_one_thing": "The loudest voices are the fewest."},
    )
    out = analysts_mod.analyze_deep_insights("Subject", {"digests": [{"themes": []}]})
    assert out["insights"][0]["headline"] == "Hidden coordination"
    assert "loudest" in out["the_one_thing"]


def test_deep_insights_degrades_on_llm_failure(monkeypatch):
    monkeypatch.setattr(analysts_mod.llm, "call_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = analysts_mod.analyze_deep_insights("Subject", {"digests": []})
    assert out == {"insights": [], "the_one_thing": ""}

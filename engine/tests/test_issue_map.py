from datetime import datetime

from engine.reports import analysts as analysts_mod
from engine.reports import digest as digest_mod
from engine.reports import issue_map as im


def _mentions(n):
    return [
        {
            "id": f"i{i:04d}xxxx",
            "platform": "news",
            "source_type": "article",
            "author_handle": "news.example.com",
            "text": f"Ruto commented on forestry policy number {i}. " * 4,
            "posted_at": datetime(2026, 6, 1),
            "engagement": {},
            "raw_payload": {"url": f"https://news/{i}", "intersection": True},
        }
        for i in range(n)
    ]


def test_build_issue_map_full_coverage_and_shape(monkeypatch):
    # Offline: deterministic map + analyst.
    monkeypatch.setattr(digest_mod.llm, "call_json", lambda *a, **k: {"digest": {"themes": []}})
    monkeypatch.setattr(
        analysts_mod.llm, "call_json",
        lambda *a, **k: {"involvement": "architect", "verdict": "central actor",
                         "linking_narratives": [{"narrative": "n"}], "key_actors": [{"name": "KFS"}],
                         "timeline": [{"when": "2026", "event": "e"}], "tension_or_risk": "r"},
    )
    ms = _mentions(120)
    out = im.build_issue_map("William Ruto", "forestry", mentions=ms)

    assert out["principal"] == "William Ruto"
    assert out["issue"] == "forestry"
    assert out["coverage"]["mentions_total"] == 120
    assert out["coverage"]["mentions_analyzed"] == 120  # every intersection mention read
    assert out["coverage"]["complete"] is True
    assert out["intersection"]["involvement"] == "architect"
    assert out["thin"] is False
    assert len(out["evidence_sample"]) == 15


def test_build_issue_map_thin_when_no_mentions(monkeypatch):
    monkeypatch.setattr(digest_mod.llm, "call_json", lambda *a, **k: {"digest": {}})
    out = im.build_issue_map("Nobody", "nothing", mentions=[])
    assert out["thin"] is True
    assert out["coverage"]["mentions_total"] == 0


def test_analyze_issue_intersection_degrades(monkeypatch):
    monkeypatch.setattr(analysts_mod.llm, "call_json",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = analysts_mod.analyze_issue_intersection("P", "I", {"digests": []})
    assert out["involvement"] == "" and out["linking_narratives"] == []


def test_acquire_intersection_offline_returns_empty(monkeypatch):
    # Egress blocked / GDELT fails → empty, no raise.
    monkeypatch.setattr(im.http, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no egress")))
    assert im.acquire_intersection("A", "B", datetime(2026, 1, 1), datetime(2026, 6, 1)) == []

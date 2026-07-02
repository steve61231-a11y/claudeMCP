from datetime import datetime

from engine.reports import analysts


def _mention(mid, text, source_type="post", platform="tiktok", author="someone", engagement=None):
    return {
        "id": mid,
        "platform": platform,
        "source_type": source_type,
        "author_handle": author,
        "text": text,
        "posted_at": datetime(2026, 6, 1),
        "engagement": engagement or {},
        "source_url": f"https://example.com/{mid}",
    }


def test_validate_quotes_keeps_traceable_and_drops_fabricated():
    mentions = [_mention("aaaa1111-0000", "Mbadi said the budget will not add MPesa charges this year")]
    refs = analysts._refs(mentions)

    quotes = [
        # verbatim, correct ref -> kept
        {"ref": "aaaa1111", "text": "the budget will not add MPesa charges"},
        # fabricated text, correct ref -> dropped
        {"ref": "aaaa1111", "text": "Raila Odinga endorsed the budget"},
        # unknown ref -> dropped
        {"ref": "ffff0000", "text": "the budget will not add MPesa charges"},
    ]
    valid = analysts._validate_quotes(quotes, refs)
    assert len(valid) == 1
    assert valid[0]["ref"] == "aaaa1111"
    assert valid[0]["url"] == "https://example.com/aaaa1111-0000"


def test_corpus_blob_promotes_comments_and_respects_budget():
    mentions = [
        _mention("post0001", "post text " * 5, engagement={"views": 1000}),
        _mention("post0002", "another post " * 5, engagement={"views": 500}),
        _mention("comm0001", "a grassroots comment", source_type="comment"),
    ]
    blob = analysts._corpus_blob(mentions)
    # Comment is interleaved first despite zero engagement.
    assert blob.index("comm0001") < blob.index("post0002")
    # Budget cuts off rendering.
    tiny = analysts._corpus_blob(mentions, budget_chars=80)
    assert len(tiny) <= 80


def test_verify_grounding_returns_input_on_llm_failure(monkeypatch):
    monkeypatch.setattr(analysts.llm, "call_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    prose = {"executive_brief": "text stays"}
    assert analysts.verify_grounding(prose, ["quote"]) == prose


def test_verify_grounding_applies_cleaned_sections(monkeypatch):
    monkeypatch.setattr(
        analysts.llm,
        "call_json",
        lambda *a, **k: {"cleaned": {"executive_brief": "cleaned text"}},
    )
    prose = {"executive_brief": "original with unsupported claim", "executive_summary": "s"}
    cleaned = analysts.verify_grounding(prose, [])
    assert cleaned["executive_brief"] == "cleaned text"
    assert cleaned["executive_summary"] == "s"  # missing key falls back to input

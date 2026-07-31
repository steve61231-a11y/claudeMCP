from engine.processing.entities import detect_entity_link, direct_or_alias_match, keyword_hint
from engine import llm


def test_direct_match_on_name():
    result = direct_or_alias_match("Wanjiru Kamau opened a new clinic today", "Wanjiru Kamau", [])
    assert result == {"matched": True, "match_type": "direct", "confidence": 1.0}


def test_alias_match():
    result = direct_or_alias_match("Kamau opened a new clinic today", "Wanjiru Kamau", ["Kamau"])
    assert result["match_type"] == "alias"


def test_no_match_without_name_or_keyword():
    assert direct_or_alias_match("the weather is nice today", "Wanjiru Kamau", []) is None


def test_keyword_hint_detects_title_and_region():
    assert keyword_hint("The Governor from Nakuru spoke today", ["Governor", "Nakuru"]) is True
    assert keyword_hint("the weather is nice today", ["Governor", "Nakuru"]) is False


def test_detect_entity_link_direct_skips_llm(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("LLM should not be called for a direct match")

    monkeypatch.setattr(llm, "call_json", boom)
    link = detect_entity_link("Wanjiru Kamau opened a new clinic", "Wanjiru Kamau", [], ["Governor", "Nakuru"])
    assert link["match_type"] == "direct"


def test_detect_entity_link_escalates_to_llm_for_indirect_mention(monkeypatch):
    monkeypatch.setattr(llm, "call_json", lambda prompt, max_tokens=256, model=None: {"matched": True, "confidence": 0.85})
    link = detect_entity_link(
        "The Governor from Nakuru really delivered on the new market stalls.",
        "Wanjiru Kamau",
        [],
        ["Governor", "Nakuru"],
    )
    assert link is not None
    assert link["match_type"] == "indirect_llm"
    assert link["confidence"] == 0.85


def test_detect_entity_link_no_match_when_llm_says_no(monkeypatch):
    monkeypatch.setattr(llm, "call_json", lambda prompt, max_tokens=256, model=None: {"matched": False, "confidence": 0.1})
    link = detect_entity_link(
        "The Governor of a different county spoke today.", "Wanjiru Kamau", [], ["Governor", "Nakuru"]
    )
    assert link is None

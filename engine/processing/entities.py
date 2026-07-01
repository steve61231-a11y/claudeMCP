import re

from engine import llm

_nlp = None


def get_nlp():
    global _nlp
    if _nlp is None:
        import spacy

        try:
            _nlp = spacy.load("en_core_web_sm")
        except OSError:
            _nlp = spacy.blank("en")
    return _nlp


def direct_or_alias_match(text: str, politician_name: str, aliases: list[str]) -> dict | None:
    """Cheap path: does the text literally contain the politician's name or a known alias?"""
    candidates = [politician_name, *aliases]
    lowered = text.lower()
    for candidate in candidates:
        if re.search(rf"\b{re.escape(candidate.lower())}\b", lowered):
            match_type = "direct" if candidate == politician_name else "alias"
            return {"matched": True, "match_type": match_type, "confidence": 1.0}
    return None


def keyword_hint(text: str, keywords: list[str]) -> bool:
    """Title/region keywords (e.g. 'Governor', 'Nakuru') suggest a possible indirect mention."""
    lowered = text.lower()
    return any(re.search(rf"\b{re.escape(k.lower())}\b", lowered) for k in keywords)


INDIRECT_PROMPT = """You are detecting indirect/implied references to a specific politician in social media text. The text may be in English, Swahili, or Sheng.

Politician: {name}
Known aliases: {aliases}
Known keywords (title/region/party): {keywords}

Does the text refer to the politician above, even without using their name directly (e.g. by title + region, like "the governor from Nakuru")?
The required JSON shape is: {{"matched": true|false, "confidence": 0.0-1.0, "reason": "short reason"}}"""


def detect_indirect_mention(text: str, politician_name: str, aliases: list[str], keywords: list[str]) -> dict:
    instructions = INDIRECT_PROMPT.format(
        name=politician_name, aliases=", ".join(aliases) or "none", keywords=", ".join(keywords) or "none"
    )
    try:
        result = llm.call_json_untrusted(instructions, text, expected_keys={"matched"}, max_tokens=256)
    except ValueError:
        # A malformed/injected reply must never link a mention.
        return {"matched": False, "match_type": "indirect_llm", "confidence": 0.0}
    return {
        "matched": bool(result.get("matched")),
        "match_type": "indirect_llm",
        "confidence": float(result.get("confidence", 0.0)),
    }


def detect_entity_link(text: str, politician_name: str, aliases: list[str], keywords: list[str]) -> dict | None:
    """Returns a match dict if the mention links to the politician, else None.

    Cheap regex match first; only escalates to the LLM when a keyword hints at
    an indirect reference but no direct/alias match was found.
    """
    direct = direct_or_alias_match(text, politician_name, aliases)
    if direct:
        return direct

    if keyword_hint(text, keywords):
        indirect = detect_indirect_mention(text, politician_name, aliases, keywords)
        if indirect["matched"]:
            return indirect

    return None


def extract_standard_entities(text: str) -> list[dict]:
    """Generic NER pass for media/location/org entities, independent of the politician match."""
    doc = get_nlp()(text)
    entities = []
    for ent in doc.ents:
        if ent.label_ in {"PERSON", "ORG", "GPE", "LOC"}:
            type_map = {"PERSON": "influencer", "ORG": "media", "GPE": "location", "LOC": "location"}
            entities.append({"name": ent.text, "type": type_map[ent.label_]})
    return entities

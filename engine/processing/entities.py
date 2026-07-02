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
    """Generic NER pass for person/org/location entities, independent of the politician match."""
    doc = get_nlp()(text)
    entities = []
    for ent in doc.ents:
        if ent.label_ in {"PERSON", "ORG", "GPE", "LOC"}:
            type_map = {"PERSON": "person", "ORG": "media", "GPE": "location", "LOC": "location"}
            entities.append({"name": ent.text, "type": type_map[ent.label_]})
    return entities


_NAME_PAIR_RE = None


def _capitalized_name_hint(text: str, politician_name: str) -> bool:
    """True if the text contains a Capitalized First Last pair that isn't the
    tracked politician — the no-NER stand-in for a PERSON candidate."""
    global _NAME_PAIR_RE
    import re

    if _NAME_PAIR_RE is None:
        _NAME_PAIR_RE = re.compile(r"\b([A-Z][a-z]+)\s+([A-Z][a-z]+)\b")
    politician_words = {w.lower() for w in politician_name.split()}
    for m in _NAME_PAIR_RE.finditer(text):
        words = {m.group(1).lower(), m.group(2).lower()}
        if not (words & politician_words):
            return True
    return False


PEOPLE_PROMPT = """You are mapping the people mentioned alongside a Kenyan politician in scraped media/social text. The text may be in English, Swahili, or Sheng.

Politician being tracked (exclude them from the output): {name}

Identify every OTHER named individual in the text — fellow politicians, journalists, party officials, activists, content creators. For each, give their role and affiliation ONLY if the provided text itself states or clearly implies it; otherwise use null. Do NOT fill in role, affiliation, office, or status from your own knowledge of public figures — your knowledge may be out of date (people die, lose office, switch parties). Never assert whether a person is alive, dead, or currently holds any position unless the text says so. Do not invent people who are not named in the text.

The required JSON shape is:
{{"people": [{{"name": "Full Name", "role": "journalist|politician|party official|activist|creator|other or null", "affiliation": "organisation/media house/party or null"}}]}}"""


def extract_people(text: str, politician_name: str) -> list[dict]:
    """Named individuals co-mentioned with the politician, with role/affiliation.

    spaCy NER gates the (paid) LLM call: no PERSON candidates, no LLM. The LLM
    then adds role/affiliation and filters NER noise. Returns [] on any failure
    — person extraction must never break the analysis run.
    """
    if not any(e["type"] == "person" for e in extract_standard_entities(text)):
        if "ner" in get_nlp().pipe_names:
            return []
        if not _capitalized_name_hint(text, politician_name):
            # Blank spaCy model (en_core_web_sm unavailable): fall back to a
            # cheap capitalized-name-pair heuristic so people extraction
            # doesn't silently drop to zero — it would otherwise empty the
            # people network.
            return []
    try:
        result = llm.call_json_untrusted(
            PEOPLE_PROMPT.format(name=politician_name), text, expected_keys={"people"}, max_tokens=512
        )
    except Exception:
        return []
    people = []
    politician_lower = politician_name.lower()
    for person in result.get("people") or []:
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or "").strip()
        if not name or name.lower() in politician_lower or politician_lower in name.lower():
            continue
        people.append(
            {
                "name": name,
                "role": (str(person["role"]).strip() or None) if person.get("role") else None,
                "affiliation": (str(person["affiliation"]).strip() or None) if person.get("affiliation") else None,
            }
        )
    return people

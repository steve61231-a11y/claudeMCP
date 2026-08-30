import re

from engine import llm, stages
from engine.config import settings

_nlp = None


def get_nlp():
    global _nlp
    if _nlp is None:
        import spacy

        # On a memory-constrained instance, skip the full NER model and use a
        # tiny blank pipeline. has_person_candidate handles a blank model (no
        # "ner" pipe) by falling back to a capitalized-name heuristic + the LLM,
        # so people extraction still works with a much smaller footprint.
        if settings.low_memory:
            _nlp = spacy.blank("en")
            return _nlp
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


PEOPLE_BATCH_PROMPT = """You are mapping the people mentioned alongside a Kenyan politician in scraped media/social text. The text may be in English, Swahili, or Sheng.

Politician being tracked (exclude them from every answer): {name}

For EACH numbered item below, identify every OTHER named individual in that item — fellow politicians, journalists, party officials, activists, content creators. For each person give their role and affiliation ONLY if that item's text states or clearly implies it; otherwise use null. Do NOT fill in role, affiliation, office, or status from your own knowledge of public figures — your knowledge may be out of date (people die, lose office, switch parties). Never assert whether a person is alive, dead, or currently holds any position unless the text says so. Do not invent people who are not named in the text.

Keep each person attached to the item number they came from. An item with no other named people simply has no entries.

Items:
{batch}

Respond with ONLY this JSON, keeping the item numbers:
{{"people": [{{"i": 1, "name": "Full Name", "role": "journalist|politician|party official|activist|creator|other or null", "affiliation": "organisation/media house/party or null"}}]}}"""

_PEOPLE_BATCH_WORKERS = 4
_PEOPLE_MAX_ITEM_CHARS = 900


def _clean_person(person, politician_lower: str) -> dict | None:
    if not isinstance(person, dict):
        return None
    name = str(person.get("name") or "").strip()
    if not name or name.lower() in politician_lower or politician_lower in name.lower():
        return None
    return {
        "name": name,
        "role": (str(person["role"]).strip() or None) if person.get("role") else None,
        "affiliation": (str(person["affiliation"]).strip() or None) if person.get("affiliation") else None,
    }


def has_person_candidate(text: str, politician_name: str) -> bool:
    """Local NER gate — the free filter that decides whether an item is worth
    sending to the model at all."""
    if any(e["type"] == "person" for e in extract_standard_entities(text)):
        return True
    if "ner" in get_nlp().pipe_names:
        return False
    # Blank spaCy model (en_core_web_sm unavailable): fall back to a cheap
    # capitalized-name-pair heuristic so people extraction doesn't silently
    # drop to zero — it would otherwise empty the people network.
    return _capitalized_name_hint(text, politician_name)


def _extract_people_batch(items: list[tuple[str, str]], politician_name: str) -> dict[str, list[dict]]:
    """One call for a batch of items. A failed batch contributes nothing."""
    lines = []
    for position, (_, text) in enumerate(items, start=1):
        snippet = (text or "").replace("\n", " ")[:_PEOPLE_MAX_ITEM_CHARS]
        lines.append(f"[{position}] {snippet}")
    batch = "\n".join(lines)

    try:
        result = llm.call_json_untrusted(
            PEOPLE_BATCH_PROMPT.format(name=politician_name, batch=batch),
            batch,
            expected_keys={"people"},
            max_tokens=min(8000, 160 * len(items) + 400),
            max_untrusted_chars=len(batch) + 1000,
            model=llm.bulk_model(),
        )
    except Exception as exc:  # noqa: BLE001 — retried on the next incremental run
        # Every person named in this batch of mentions is lost. Silently, the
        # people network and "key people" just come back smaller.
        stages.current().failed(f"people_extraction[{len(items)}]", exc)
        return {}

    politician_lower = politician_name.lower()
    out: dict[str, list[dict]] = {}
    for entry in result.get("people") or []:
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= position <= len(items):
            continue
        person = _clean_person(entry, politician_lower)
        if person:
            out.setdefault(items[position - 1][0], []).append(person)
    return out


def extract_people_items(items: list[tuple[str, str]], politician_name: str) -> dict[str, list[dict]]:
    """People co-mentioned with the politician, for a whole corpus at once.

    This was the last stage still spending one LLM round-trip per mention, and
    at a few hundred mentions it dominated both the cost and the wall-clock of
    a report — on a rate-limited backend it was the whole run. Batching it the
    way sentiment was batched turns ~300 calls into ~12.

    spaCy NER still gates the call per item (no PERSON candidate, no tokens
    spent), so a batch only carries items that might actually contain someone.
    Returns {item_id: people}; an item the model doesn't answer for is simply
    absent, so a later run retries it rather than recording an empty answer.
    """
    from concurrent.futures import ThreadPoolExecutor

    from engine.config import settings

    candidates = [(item_id, text) for item_id, text in items
                  if (text or "").strip() and has_person_candidate(text, politician_name)]
    if not candidates:
        return {}

    size = max(1, settings.agent_batch_size)
    batches = [candidates[i : i + size] for i in range(0, len(candidates), size)]
    people: dict[str, list[dict]] = {}
    workers = llm.concurrency(min(_PEOPLE_BATCH_WORKERS, len(batches)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for partial in pool.map(lambda b: _extract_people_batch(b, politician_name), batches):
            people.update(partial)
    return people

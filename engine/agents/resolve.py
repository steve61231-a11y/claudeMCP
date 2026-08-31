"""Entity & event resolution — turning coverage into things that happened.

A corpus is a pile of *reporting*. Forty articles about one contract award are
forty documents but ONE event, and reading them as forty is how a system
mistakes repetition for significance: the loudest story wins rather than the
best-corroborated one.

This stage converts documents and mentions into the units an analyst actually
reasons about:

  entities — the people, organisations, companies, places, policies and
             contracts that appear, tracked across runs with first/last seen so
             a name showing up for the FIRST time is detectable,
  events   — discrete happenings, deduplicated across every source that
             reported them, carrying their evidence.

Confidence comes from INDEPENDENT corroboration: how many distinct domains
reported it, not how many copies exist. Ten syndications of one wire story
remain one source, and an event asserted by exactly one outlet stays visibly
single-sourced rather than being promoted to fact.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from engine import llm, stages
from engine.config import settings
from engine.db.models import Entity, Event, EventEvidence

# Entity types we resolve. Deliberately wider than "people": due diligence turns
# on companies, contracts and policies as much as on individuals.
ENTITY_TYPES = (
    "person", "organization", "company", "location", "policy",
    "contract", "event", "party", "media",
)

_MAX_ITEM_CHARS = 1200
_WORKERS = 4


EXTRACT_PROMPT = """You are building an intelligence file on {subject}.

From EACH numbered source below, extract:

1. entities — every named person, organisation, company, place, policy,
   contract or party mentioned, and how each relates to {subject}.
2. events — discrete things that HAPPENED (an appointment, a court ruling, a
   contract award, a resignation, a meeting, an allegation being made). Give
   each a short factual title, the date if the text states or implies one, and
   the type.

Rules:
- Extract only what the text supports. Do not add anything from your own
  knowledge, and do not infer people or events that are not described.
- A title should describe the happening, not the coverage: "Ministry awards
  Mombasa terminal contract", not "Article discusses contract".
- If a source contains no real event, return an empty events list for it.

Sources:
{batch}

Respond with ONLY this JSON:
{{"results": [{{"i": 1,
   "entities": [{{"name": "...", "type": "person|organization|company|location|policy|contract|party|media", "relation": "how they relate to {subject}"}}],
   "events": [{{"title": "...", "date": "YYYY-MM-DD or null", "type": "appointment|court|contract|statement|meeting|allegation|resignation|other", "summary": "one sentence"}}]}}]}}"""


def _extract_batch(subject: str, items: list[dict]) -> dict[int, dict]:
    """Extract entities/events from one batch of corpus items."""
    lines = []
    for position, item in enumerate(items, start=1):
        text = (item.get("text") or "").replace("\n", " ")[:_MAX_ITEM_CHARS]
        lines.append(f"[{position}] SOURCE: {item.get('source') or 'unknown'}\n     {text}")
    batch = "\n".join(lines)

    try:
        result = llm.call_json_untrusted(
            EXTRACT_PROMPT.format(subject=subject, batch=batch),
            batch,
            expected_keys={"results"},
            max_tokens=llm.budget_for(400 * len(items) + 600),
            max_untrusted_chars=len(batch) + 1000,
            model=llm.bulk_model(),
        )
    except Exception as exc:  # noqa: BLE001 — a failed batch is retried next run
        stages.current().failed(f"event_resolution[{len(items)}]", exc)
        return {}

    out: dict[int, dict] = {}
    for entry in result.get("results") or []:
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if 1 <= position <= len(items):
            out[position - 1] = {
                "entities": entry.get("entities") or [],
                "events": entry.get("events") or [],
            }
    return out


def canonical_key(name: str, entity_type: str) -> str:
    """Stable identity for an entity across runs and spellings."""
    normalized = re.sub(r"[^\w\s]", "", (name or "").lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return f"{entity_type}:{normalized}"


def _stem(word: str) -> str:
    """Crude suffix stripping so tense and number don't split one event in two.

    Headlines say "awards", "awarded" and "awarding" for the same happening; a
    key that treats those as different words would file one event three times.
    A full stemmer is overkill here — matching the substantive nouns is what
    actually decides identity.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            stem = word[: -len(suffix)]
            # "cancelled" -> "cancell" -> "cancel", matching "cancels".
            if len(stem) > 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                stem = stem[:-1]
            return stem
    return word


def event_dedupe_key(title: str, occurred_at: datetime | None) -> str:
    """Identity for a happening, so the same event reported by many outlets
    resolves to one row.

    Keyed on the distinctive (stemmed) words of the title plus the day when
    known: outlets word headlines differently but rarely disagree on the
    substantive nouns or the date.
    """
    words = re.sub(r"[^\w\s]", " ", (title or "").lower()).split()
    stop = {"the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or",
            "as", "by", "with", "from", "is", "was", "says", "said", "after",
            "been", "be", "has", "have", "had", "will", "its", "their"}
    salient = sorted({_stem(w) for w in words if len(w) > 3 and w not in stop})[:6]
    day = occurred_at.date().isoformat() if occurred_at else "undated"
    return f"{day}|{'-'.join(salient)}"


def _parse_date(value) -> datetime | None:
    if not value or str(value).lower() in ("null", "none", "unknown"):
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        return None


def resolve_corpus(db, politician, corpus: list[dict], limit: int | None = None) -> dict:
    """Extract entities and events from the corpus and persist them.

    Idempotent by construction: entities dedupe on canonical_key and events on
    their dedupe key, so re-running enriches (more evidence, wider first/last
    seen) instead of duplicating.
    """
    items = corpus[: (limit or settings.resolution_max_items)]
    if not items:
        return {"items": 0, "entities": 0, "events": 0, "evidence": 0}

    size = max(1, settings.agent_batch_size // 3)  # richer output per item
    batches = [items[i : i + size] for i in range(0, len(items), size)]
    with ThreadPoolExecutor(max_workers=llm.concurrency(min(_WORKERS, len(batches)))) as pool:
        extracted = list(pool.map(lambda b: _extract_batch(politician.name, b), batches))

    now = datetime.utcnow()
    entity_cache: dict[str, Entity] = {}
    event_cache: dict[str, Event] = {}
    new_entities = new_events = new_evidence = 0

    for batch, results in zip(batches, extracted):
        for offset, payload in results.items():
            if offset >= len(batch):
                continue
            item = batch[offset]

            for raw in payload["entities"]:
                name = str(raw.get("name") or "").strip()
                etype = str(raw.get("type") or "person").strip().lower()
                if not name or etype not in ENTITY_TYPES:
                    continue
                key = canonical_key(name, etype)
                entity = entity_cache.get(key)
                if entity is None:
                    entity = db.query(Entity).filter_by(canonical_key=key).first()
                if entity is None:
                    entity = Entity(
                        name=name, type=etype, canonical_key=key,
                        first_seen=now, last_seen=now, mention_count=0,
                        attributes={"relation": str(raw.get("relation") or "")[:300]},
                    )
                    db.add(entity)
                    db.flush()
                    new_entities += 1
                else:
                    entity.last_seen = now
                    if entity.first_seen is None:
                        entity.first_seen = now
                entity.mention_count = (entity.mention_count or 0) + 1
                entity_cache[key] = entity

            for raw in payload["events"]:
                title = str(raw.get("title") or "").strip()
                if not title:
                    continue
                occurred = _parse_date(raw.get("date")) or item.get("posted_at")
                key = event_dedupe_key(title, occurred)
                event = event_cache.get(key)
                if event is None:
                    event = (
                        db.query(Event)
                        .filter_by(politician_id=politician.id, dedupe_key=key)
                        .first()
                    )
                if event is None:
                    event = Event(
                        politician_id=politician.id,
                        title=title[:500],
                        summary=str(raw.get("summary") or "")[:2000],
                        event_type=str(raw.get("type") or "other")[:50],
                        occurred_at=occurred,
                        occurred_precision="day" if raw.get("date") else "unknown",
                        dedupe_key=key,
                        first_seen=now,
                        last_seen=now,
                        corroboration_count=0,
                        independent_domains=0,
                    )
                    db.add(event)
                    db.flush()
                    new_events += 1
                else:
                    event.last_seen = now
                event_cache[key] = event

                linked = _link_evidence(db, event, item)
                new_evidence += 1 if linked else 0

    # Corroboration is recomputed from the stored evidence, so it always
    # reflects DISTINCT sources rather than a running tally of duplicates.
    # Sessions here run with autoflush off, so make the new evidence visible
    # to the recount first.
    db.flush()
    for event in event_cache.values():
        _recompute_corroboration(db, event)

    db.commit()
    return {
        "items": len(items),
        "entities": new_entities,
        "events": new_events,
        "evidence": new_evidence,
    }


def _link_evidence(db, event: Event, item: dict) -> bool:
    """Attach the corpus item to the event, once."""
    item_id = item.get("id")
    if not item_id:
        return False
    is_document = item.get("source_type") == "article"
    existing = (
        db.query(EventEvidence)
        .filter_by(
            event_id=event.id,
            document_id=item_id if is_document else None,
            mention_id=None if is_document else item_id,
        )
        .first()
    )
    if existing:
        return False
    db.add(
        EventEvidence(
            event_id=event.id,
            document_id=item_id if is_document else None,
            mention_id=None if is_document else item_id,
            quote=(item.get("text") or "")[:1000],
            role="corroborating",
        )
    )
    return True


def _recompute_corroboration(db, event: Event) -> None:
    """Confidence from independent sources, not from repetition.

    A story carried by three unrelated outlets is materially stronger than the
    same wire copy syndicated thirty times, and the score has to say so.
    """
    from engine.db.models import Document, RawMention

    rows = db.query(EventEvidence).filter_by(event_id=event.id).all()
    domains: set[str] = set()
    for row in rows:
        if row.document_id:
            doc = db.get(Document, row.document_id)
            if doc and doc.domain:
                domains.add(doc.domain.lower())
        elif row.mention_id:
            mention = db.get(RawMention, row.mention_id)
            if mention and mention.platform:
                domains.add(mention.platform.lower())

    event.corroboration_count = len(rows)
    event.independent_domains = len(domains)
    event.confidence = {0: 0.3, 1: 0.5, 2: 0.7}.get(len(domains), 0.9)

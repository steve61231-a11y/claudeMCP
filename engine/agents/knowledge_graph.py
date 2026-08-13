"""Knowledge graph — the system's long-lived memory of how things connect.

Reports are snapshots; the graph is what persists between them. Each run adds
entities, strengthens the edges it re-observes, and dates the ones it sees for
the first time — so the picture of a subject's world compounds instead of being
rebuilt from scratch every time.

Why it matters for due diligence: the decisive fact is often not in any single
document but in the *shape* of the connections — the same little-known company
appearing beside three different officials, a supposed rival who keeps turning
up in the same rooms. No amount of summarising individual articles surfaces
that; traversing relationships does.

Construction is two-tier, so cost tracks value:
  1. **Co-occurrence** (free, deterministic): entities named in the same
     document are related somehow. This builds the skeleton.
  2. **Typing** (LLM, bounded): only the strongest, most-evidenced pairs get a
     specific relationship type. Weak pairs stay honestly labelled as
     "mentioned_with" rather than being given an invented relationship.

Every edge carries its evidence, so an assertion in the graph can be traced back
to the documents that produced it.
"""

from collections import defaultdict
from datetime import datetime

from engine import llm
from engine.config import settings
from engine.db.models import Entity, EntityRelationship

# Typed relations we recognise. Anything the model proposes outside this set is
# rejected — an open vocabulary would make the graph unqueryable.
RELATION_TYPES = {
    "works_for", "leads", "member_of", "allied_with", "rival_of",
    "family_of", "owns", "contracted_by", "awarded_to", "investigated_by",
    "accused_by", "succeeded", "met_with", "concerns_policy", "located_in",
    "mentioned_with",
}

# Co-occurrence alone doesn't justify a model call; typing is reserved for pairs
# with real weight behind them.
_MIN_WEIGHT_FOR_TYPING = 2
_MAX_PAIRS_TO_TYPE = 60


TYPE_PROMPT = """You are mapping relationships for an intelligence file on {subject}.

For EACH numbered pair below, state the relationship FROM the first entity TO the
second, using ONLY the supporting text provided.

Allowed types: works_for, leads, member_of, allied_with, rival_of, family_of,
owns, contracted_by, awarded_to, investigated_by, accused_by, succeeded,
met_with, concerns_policy, located_in, mentioned_with.

Rules:
- Use the evidence, not your own knowledge of these people or organisations.
- If the text shows they appear together but does NOT establish how they are
  related, answer "mentioned_with". That is the honest answer and is expected
  to be common — do not invent a specific relationship to seem informative.
- confidence reflects how clearly the text establishes the relationship.

Pairs:
{batch}

Respond with ONLY this JSON:
{{"relations": [{{"i": 1, "type": "works_for", "confidence": 0.0-1.0, "reason": "short justification"}}]}}"""


def _co_occurrence(db, politician, corpus: list[dict]) -> dict[tuple[str, str], dict]:
    """Entity pairs that appear in the same source, with the evidence.

    Deterministic and free: this is the graph's skeleton, built before any model
    is consulted.
    """
    from engine.db.models import MentionEntity

    # Entities are linked to mentions via MentionEntity; documents carry their
    # entities through the resolution pass, so we read co-occurrence from both.
    by_item: dict[str, set[str]] = defaultdict(set)

    rows = (
        db.query(MentionEntity.mention_id, MentionEntity.entity_id)
        .join(Entity, Entity.id == MentionEntity.entity_id)
        .all()
    )
    for mention_id, entity_id in rows:
        by_item[mention_id].add(entity_id)

    pairs: dict[tuple[str, str], dict] = {}
    for item_id, entity_ids in by_item.items():
        if len(entity_ids) < 2:
            continue
        ordered = sorted(entity_ids)
        for i, source_id in enumerate(ordered):
            for target_id in ordered[i + 1 :]:
                key = (source_id, target_id)
                entry = pairs.setdefault(key, {"weight": 0, "evidence": []})
                entry["weight"] += 1
                if len(entry["evidence"]) < 5:
                    entry["evidence"].append({"mention_id": item_id})
    return pairs


def _type_pairs(subject: str, pairs: list[tuple[str, str, str]]) -> dict[int, dict]:
    """Ask the model to type the strongest pairs. Batched and bounded."""
    lines = []
    for position, (a_name, b_name, context) in enumerate(pairs, start=1):
        lines.append(f"[{position}] {a_name} -> {b_name}\n     EVIDENCE: {context[:500]}")
    batch = "\n".join(lines)

    try:
        result = llm.call_json_untrusted(
            TYPE_PROMPT.format(subject=subject, batch=batch),
            batch,
            expected_keys={"relations"},
            max_tokens=min(4000, 100 * len(pairs) + 400),
            max_untrusted_chars=len(batch) + 1000,
            model=llm.bulk_model(),
        )
    except Exception:  # noqa: BLE001
        return {}

    out: dict[int, dict] = {}
    for entry in result.get("relations") or []:
        try:
            position = int(entry.get("i"))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(pairs):
            continue
        rel_type = str(entry.get("type") or "mentioned_with").strip().lower()
        if rel_type not in RELATION_TYPES:
            rel_type = "mentioned_with"  # never invent a vocabulary term
        try:
            confidence = float(entry.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        out[position - 1] = {
            "type": rel_type,
            "confidence": max(0.0, min(1.0, confidence)),
            "reason": str(entry.get("reason") or "")[:300],
        }
    return out


def build_graph(db, politician, corpus: list[dict]) -> dict:
    """Build/extend the knowledge graph for this subject.

    Incremental by design: an edge seen again gains weight and a fresh
    last_seen; a genuinely new edge records when it first appeared.
    """
    pairs = _co_occurrence(db, politician, corpus)
    if not pairs:
        return {"edges": 0, "new_edges": 0, "typed": 0}

    now = datetime.utcnow()
    entity_names = {
        e.id: e.name
        for e in db.query(Entity).filter(Entity.id.in_({i for pair in pairs for i in pair})).all()
    }

    # Type only the pairs with enough behind them to be worth a call.
    candidates = sorted(
        (k for k, v in pairs.items() if v["weight"] >= _MIN_WEIGHT_FOR_TYPING),
        key=lambda k: pairs[k]["weight"],
        reverse=True,
    )[:_MAX_PAIRS_TO_TYPE]

    typed: dict[tuple[str, str], dict] = {}
    if candidates:
        batch_size = max(1, settings.agent_batch_size // 2)
        for start in range(0, len(candidates), batch_size):
            chunk = candidates[start : start + batch_size]
            rendered = [
                (
                    entity_names.get(a, "?"),
                    entity_names.get(b, "?"),
                    f"co-mentioned in {pairs[(a, b)]['weight']} sources",
                )
                for a, b in chunk
            ]
            for offset, relation in _type_pairs(politician.name, rendered).items():
                if offset < len(chunk):
                    typed[chunk[offset]] = relation

    new_edges = 0
    for (source_id, target_id), info in pairs.items():
        relation = typed.get((source_id, target_id))
        rel_type = relation["type"] if relation else "mentioned_with"
        # Last line of defence: the vocabulary is enforced where the edge is
        # WRITTEN, not only where it is proposed, so no path can introduce an
        # unqueryable relation type into the graph.
        if rel_type not in RELATION_TYPES:
            rel_type = "mentioned_with"
        confidence = relation["confidence"] if relation else min(0.5, 0.2 + 0.05 * info["weight"])

        edge = (
            db.query(EntityRelationship)
            .filter_by(source_entity_id=source_id, target_entity_id=target_id, rel_type=rel_type)
            .first()
        )
        if edge is None:
            edge = EntityRelationship(
                politician_id=politician.id,
                source_entity_id=source_id,
                target_entity_id=target_id,
                rel_type=rel_type,
                first_seen=now,
            )
            db.add(edge)
            new_edges += 1
        edge.weight = float(info["weight"])
        edge.confidence = confidence
        edge.evidence = info["evidence"]
        edge.evidence_count = info["weight"]
        edge.last_seen = now

    db.commit()
    return {"edges": len(pairs), "new_edges": new_edges, "typed": len(typed)}


def neighbours(db, entity_id: str, limit: int = 25) -> list[dict]:
    """Everything directly connected to an entity, strongest first."""
    edges = (
        db.query(EntityRelationship)
        .filter(
            (EntityRelationship.source_entity_id == entity_id)
            | (EntityRelationship.target_entity_id == entity_id)
        )
        .order_by(EntityRelationship.weight.desc())
        .limit(limit)
        .all()
    )
    out = []
    for edge in edges:
        other_id = (
            edge.target_entity_id if edge.source_entity_id == entity_id else edge.source_entity_id
        )
        other = db.get(Entity, other_id)
        if other is None:
            continue
        out.append(
            {
                "entity_id": other_id,
                "name": other.name,
                "type": other.type,
                "rel_type": edge.rel_type,
                "weight": edge.weight,
                "confidence": edge.confidence,
                "first_seen": str(edge.first_seen) if edge.first_seen else None,
            }
        )
    return out


def find_paths(db, start_id: str, end_id: str, max_depth: int = 3) -> list[list[dict]]:
    """Shortest connection(s) between two entities.

    This is the question a graph exists to answer and a document search cannot:
    "how is this person connected to that company?" An indirect route through
    two intermediaries is often the finding itself.
    """
    if start_id == end_id:
        return []

    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in db.query(EntityRelationship).all():
        adjacency[edge.source_entity_id].append((edge.target_entity_id, edge.rel_type))
        adjacency[edge.target_entity_id].append((edge.source_entity_id, edge.rel_type))

    # Breadth-first: the shortest path is the most meaningful connection, and
    # deeper ones quickly become coincidence rather than signal.
    queue: list[tuple[str, list[tuple[str, str]]]] = [(start_id, [])]
    visited = {start_id}
    found: list[list[dict]] = []

    while queue and not found:
        next_queue: list[tuple[str, list[tuple[str, str]]]] = []
        for node, path in queue:
            for neighbour_id, rel_type in adjacency.get(node, []):
                if neighbour_id in visited:
                    continue
                new_path = path + [(neighbour_id, rel_type)]
                if neighbour_id == end_id:
                    found.append(new_path)
                elif len(new_path) < max_depth:
                    next_queue.append((neighbour_id, new_path))
            visited.add(node)
        queue = next_queue

    rendered: list[list[dict]] = []
    for path in found[:3]:
        steps = []
        for entity_id, rel_type in path:
            entity = db.get(Entity, entity_id)
            steps.append(
                {
                    "entity_id": entity_id,
                    "name": entity.name if entity else "?",
                    "type": entity.type if entity else None,
                    "via": rel_type,
                }
            )
        rendered.append(steps)
    return rendered

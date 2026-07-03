"""Network 2.0: the full people-relationship graph around a politician.

Everything is computed from the stored corpus (mentions + entity links +
sentiment + author profiles), with two batched LLM passes adding meaning:
typed relationships between the strongest person-pairs (rival/ally/defends/
attacks/…, each with verbatim evidence) and grounded per-person profiles
(role/relationship per sources, plus flags such as deceased_per_sources —
the "Raila is dead" accuracy fix: status comes ONLY from the corpus).
"""

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from engine import llm
from engine.db.models import (
    AuthorProfile,
    Entity,
    MentionEntity,
    MentionSentiment,
    Politician,
    RawMention,
)
from engine.reports.analysts import GROUNDING_RULES

MAX_PERSON_NODES = 120
MAX_AUTHOR_NODES = 30
PAIRS_TO_CLASSIFY = 40
PAIR_BATCH = 20
PROFILE_BATCH = 25
RECENT_DAYS = 7


def _eng(m: RawMention) -> int:
    e = m.engagement_json or {}
    return sum(int(e.get(k) or 0) for k in ("views", "likes", "shares", "comments"))


def _influence_weight(mentions: list[RawMention]) -> float:
    # log-damped engagement so one mega-viral video doesn't flatten everyone else.
    return round(sum(1 + math.log10(1 + _eng(m)) for m in mentions), 1)


def _stance(mentions: list[RawMention], sentiments: dict[str, str]) -> str:
    labels = [sentiments.get(m.id) for m in mentions]
    labels = [l for l in labels if l]
    if len(labels) < 2:
        return "neutral"
    pos = labels.count("positive") / len(labels)
    neg = labels.count("negative") / len(labels)
    if pos >= 0.4 and neg >= 0.4:
        return "contested"
    if neg >= 0.45:
        return "critic-context"
    if pos >= 0.45:
        return "ally-context"
    return "neutral"


def build_people_graph(db: Session, politician: Politician, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()

    politician_entity = (
        db.query(Entity).filter_by(type="politician", name=politician.name).first()
    )
    if politician_entity is None:
        return {"nodes": [], "edges": [], "clusters": [], "insights": {}}

    subject_mention_ids = {
        mid
        for (mid,) in db.query(MentionEntity.mention_id)
        .join(RawMention, RawMention.id == MentionEntity.mention_id)
        .filter(
            MentionEntity.entity_id == politician_entity.id,
            RawMention.politician_id == politician.id,
        )
        .all()
    }

    # person -> the subject-linked mentions they appear in
    rows = (
        db.query(Entity, RawMention)
        .join(MentionEntity, MentionEntity.entity_id == Entity.id)
        .join(RawMention, RawMention.id == MentionEntity.mention_id)
        .filter(Entity.type == "person", MentionEntity.mention_id.in_(subject_mention_ids))
        .all()
    )
    person_mentions: dict[str, list[RawMention]] = defaultdict(list)
    entities_by_name: dict[str, Entity] = {}
    for entity, mention in rows:
        person_mentions[entity.name].append(mention)
        entities_by_name[entity.name] = entity

    mention_ids_all = {m.id for ms in person_mentions.values() for m in ms} | subject_mention_ids
    sentiments = {
        mid: label
        for mid, label in db.query(MentionSentiment.mention_id, MentionSentiment.sentiment)
        .filter(MentionSentiment.mention_id.in_(mention_ids_all))
        .all()
    }

    ranked_people = sorted(person_mentions.items(), key=lambda kv: _influence_weight(kv[1]), reverse=True)
    ranked_people = ranked_people[:MAX_PERSON_NODES]
    person_set = {name for name, _ in ranked_people}

    nodes = [
        {
            "id": "subject",
            "label": politician.name,
            "node_type": "subject",
            "influence": max((_influence_weight(ms) for _, ms in ranked_people), default=10) * 1.3,
            "stance": "subject",
            "recent_activity": True,
            "mention_count": len(subject_mention_ids),
        }
    ]
    recent_cutoff = now - timedelta(days=RECENT_DAYS)
    for name, ms in ranked_people:
        entity = entities_by_name[name]
        nodes.append(
            {
                "id": name,
                "label": name,
                "node_type": "politician" if (entity.role or "") == "politician" else "public_figure",
                "role": entity.role,
                "affiliation": entity.affiliation,
                "influence": _influence_weight(ms),
                "stance": _stance(ms, sentiments),
                "recent_activity": any(m.posted_at and m.posted_at >= recent_cutoff for m in ms),
                "mention_count": len(ms),
            }
        )

    # Author nodes: media houses + influencers actually driving the subject's coverage.
    author_rows = (
        db.query(RawMention.author_handle, func.count(RawMention.id))
        .filter(RawMention.id.in_(subject_mention_ids), RawMention.author_handle.isnot(None))
        .group_by(RawMention.author_handle)
        .order_by(func.count(RawMention.id).desc())
        .limit(MAX_AUTHOR_NODES)
        .all()
    )
    profiles = {
        p.handle: p
        for p in db.query(AuthorProfile)
        .filter(AuthorProfile.handle.in_([h for h, _ in author_rows]))
        .all()
    }
    for handle, count in author_rows:
        if count < 3 or handle in person_set:
            continue
        profile = profiles.get(handle)
        followers = profile.follower_count if profile else None
        nodes.append(
            {
                "id": f"@{handle}",
                "label": f"@{handle}",
                "node_type": "influencer" if (followers or 0) >= 1000 else "media_or_author",
                "followers": followers,
                "influence": round(count * 1.5, 1),
                "stance": "neutral",
                "recent_activity": False,
                "mention_count": int(count),
            }
        )
        # talks_about edge: author -> subject (they drive coverage volume)
        # rendered as part of edges below.

    # --- Edges ---
    edges: list[dict] = []
    for name, ms in ranked_people:
        edges.append(
            {
                "source": "subject",
                "target": name,
                "kind": "co_mentioned",
                "weight": len(ms),
            }
        )
    for handle, count in author_rows:
        if count < 3 or handle in person_set:
            continue
        edges.append({"source": f"@{handle}", "target": "subject", "kind": "talks_about", "weight": int(count)})

    # person↔person pairs with trend
    mention_lookup: dict[str, RawMention] = {m.id: m for ms in person_mentions.values() for m in ms}
    pair_mentions: dict[tuple[str, str], list[RawMention]] = defaultdict(list)
    mention_people: dict[str, list[str]] = defaultdict(list)
    for name, ms in ranked_people:
        for m in ms:
            mention_people[m.id].append(name)
    for mid, names in mention_people.items():
        unique = sorted(set(names))
        for i in range(len(unique)):
            for j in range(i + 1, len(unique)):
                pair_mentions[(unique[i], unique[j])].append(mention_lookup[mid])

    trend_cutoff = now - timedelta(days=14)
    pair_list = sorted(pair_mentions.items(), key=lambda kv: len(kv[1]), reverse=True)
    for (a, b), ms in pair_list:
        if len(ms) < 2:
            continue
        recent = sum(1 for m in ms if m.posted_at and m.posted_at >= trend_cutoff)
        edges.append(
            {
                "source": a,
                "target": b,
                "kind": "co_mentioned",
                "weight": len(ms),
                "trend": recent - (len(ms) - recent),
            }
        )

    # Dense corpora can produce thousands of pair edges — cap by weight so the
    # payload and the physics simulation stay bounded (structural edges to the
    # subject are never dropped).
    structural = [e for e in edges if e["source"] == "subject" or e["target"] == "subject"]
    pair_edges = [e for e in edges if e not in structural]
    pair_edges.sort(key=lambda e: e["weight"], reverse=True)
    edges = structural + pair_edges[:400]

    # --- LLM layer: typed relationships + grounded profiles ---
    # Include subject↔person pairs so "top rival/defender OF THE SUBJECT" is
    # grounded in classified edges, not inferred sideways. The whole layer is
    # best-effort: any failure degrades to an untyped graph, never an error.
    subject_pairs = [(("subject", name), ms[:4]) for name, ms in ranked_people[:12]]
    try:
        relationship_edges = _classify_pairs(
            politician.name, subject_pairs + pair_list[:PAIRS_TO_CLASSIFY], person_mentions
        )
    except Exception:
        relationship_edges = []
    edge_index = {(e["source"], e["target"]): e for e in edges}
    for rel in relationship_edges:
        key = (rel["a"], rel["b"])
        edge = edge_index.get(key) or edge_index.get((key[1], key[0]))
        if edge is not None:
            edge["relationship"] = rel["relationship"]
            edge["evidence"] = rel["evidence"]

    try:
        profiles_by_name = _profile_people(politician.name, ranked_people[:50], person_mentions)
    except Exception:
        profiles_by_name = {}
    for node in nodes:
        prof = profiles_by_name.get(node["id"])
        if prof:
            node["context_note"] = prof.get("relationship_to_subject")
            node["role_per_sources"] = prof.get("role_per_sources")
            node["flags"] = prof.get("flags", [])
            if "deceased_per_sources" in node["flags"] or "legacy_figure" in node["flags"]:
                node["node_type"] = "legacy_figure"
            entity = entities_by_name.get(node["id"])
            if entity is not None and prof.get("role_per_sources") and not entity.role:
                entity.role = prof["role_per_sources"][:80]
    db.commit()

    clusters = _clusters(nodes, edges)
    insights = _insights(nodes, edges)

    return {
        "generated_at": str(now),
        "nodes": nodes,
        "edges": edges,
        "clusters": clusters,
        "insights": insights,
        "legend": {
            "stance": {
                "ally-context": "#86efac",
                "critic-context": "#fb7185",
                "contested": "#fbbf24",
                "neutral": "#94a3b8",
                "subject": "#7dd3fc",
            },
            "node_type_notes": {
                "legacy_figure": "referenced posthumously / as legacy in sources",
                "influencer": "1k+ followers",
            },
        },
    }


PAIR_PROMPT = """You are mapping relationships between Kenyan public figures based ONLY on the scraped snippets below. The tracked politician is {name}.

{grounding}

The numbered PAIRS with their snippets are in the untrusted content. For each pair, classify the relationship the snippets show between the two people: one of rival | ally | party_colleague | family | attacks | defends | interviews | succession_dispute | unclear. Include 1-2 short verbatim evidence snippets (copied exactly) for any classification other than unclear.

Required JSON shape:
{{"pairs": [{{"index": 1, "relationship": "rival", "evidence": ["verbatim snippet"]}}]}}"""


def _classify_pairs(subject: str, pair_list: list, person_mentions: dict) -> list[dict]:
    results: list[dict] = []
    for start in range(0, len(pair_list), PAIR_BATCH):
        batch = pair_list[start : start + PAIR_BATCH]
        blocks, source_texts = [], []
        for idx, ((a, b), ms) in enumerate(batch, 1):
            snippets = [(m.text or "").strip().replace("\n", " ")[:300] for m in ms[:3]]
            source_texts.extend(snippets)
            joined = " || ".join(snippets)
            display_a = subject if a == "subject" else a
            display_b = subject if b == "subject" else b
            blocks.append(f"PAIR {idx}: {display_a} <-> {display_b}\nsnippets: {joined}")
        try:
            result = llm.call_json_untrusted(
                PAIR_PROMPT.format(name=subject, grounding=GROUNDING_RULES),
                "\n\n".join(blocks),
                expected_keys={"pairs"},
                max_tokens=2000,
                max_untrusted_chars=25000,
            )
        except Exception:
            continue
        corpus_norm = " ".join(" ".join(source_texts).split()).lower()
        for item in result.get("pairs") or []:
            if not isinstance(item, dict):
                continue
            try:
                (a, b), _ = batch[int(item.get("index", 0)) - 1]
            except (IndexError, ValueError, TypeError):
                continue
            rel = str(item.get("relationship") or "unclear")
            if rel == "unclear":
                continue
            evidence = [
                e for e in (item.get("evidence") or [])
                if isinstance(e, str) and " ".join(e.split()).lower()[:50] in corpus_norm
            ]
            if not evidence:
                continue  # no verifiable receipt, no claim
            results.append({"a": a, "b": b, "relationship": rel, "evidence": evidence[:2]})
    return results


PROFILE_PROMPT = """You are profiling people who appear in scraped coverage of the Kenyan politician {name}. Below, each numbered PERSON comes with snippets from the coverage they appear in.

{grounding}
Special rule on status: if (and ONLY if) the snippets indicate a person is deceased or being referred to as a legacy/posthumous figure (e.g. "the late", "post-X era", mourning language), add the flag "deceased_per_sources". Never infer life status from your own knowledge.

The numbered PERSON entries with their snippets are in the untrusted content. For each person return: role_per_sources (what the snippets say they are, else null), relationship_to_subject (one grounded sentence about how they relate to {name} in this coverage), flags (subset of ["deceased_per_sources", "legacy_figure", "rising", "contested"]) and for every flag a short verbatim supporting snippet.

Required JSON shape:
{{"people": [{{"index": 1, "role_per_sources": "...", "relationship_to_subject": "...", "flags": ["..."], "flag_evidence": ["verbatim snippet"]}}]}}"""


def _profile_people(subject: str, ranked_people: list, person_mentions: dict) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for start in range(0, len(ranked_people), PROFILE_BATCH):
        batch = ranked_people[start : start + PROFILE_BATCH]
        blocks, source_texts = [], []
        for idx, (name, ms) in enumerate(batch, 1):
            snippets = [(m.text or "").strip().replace("\n", " ")[:250] for m in ms[:3]]
            source_texts.extend(snippets)
            blocks.append(f"PERSON {idx}: {name}\nsnippets: " + " || ".join(snippets))
        try:
            result = llm.call_json_untrusted(
                PROFILE_PROMPT.format(name=subject, grounding=GROUNDING_RULES),
                "\n\n".join(blocks),
                expected_keys={"people"},
                max_tokens=2500,
                max_untrusted_chars=25000,
            )
        except Exception:
            continue
        corpus_norm = " ".join(" ".join(source_texts).split()).lower()
        for item in result.get("people") or []:
            if not isinstance(item, dict):
                continue
            try:
                name, _ = batch[int(item.get("index", 0)) - 1]
            except (IndexError, ValueError, TypeError):
                continue
            flags = [f for f in (item.get("flags") or []) if isinstance(f, str)]
            flag_evidence = [
                e for e in (item.get("flag_evidence") or [])
                if isinstance(e, str) and " ".join(e.split()).lower()[:40] in corpus_norm
            ]
            if flags and not flag_evidence:
                flags = []  # unsupported flags are dropped wholesale
            profiles[name] = {
                "role_per_sources": item.get("role_per_sources"),
                "relationship_to_subject": item.get("relationship_to_subject"),
                "flags": flags,
                "flag_evidence": flag_evidence[:2],
            }
    return profiles


def _clusters(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Connected-community grouping via label propagation on person-person
    co-mention weights (subject and author nodes excluded from seeding)."""
    person_ids = {n["id"] for n in nodes if n["node_type"] in ("politician", "public_figure", "legacy_figure")}
    neighbors: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for e in edges:
        if e["source"] in person_ids and e["target"] in person_ids:
            neighbors[e["source"]].append((e["target"], e["weight"]))
            neighbors[e["target"]].append((e["source"], e["weight"]))

    labels = {pid: pid for pid in person_ids}
    for _ in range(6):
        changed = False
        for pid in sorted(person_ids):
            votes: dict[str, int] = defaultdict(int)
            for other, w in neighbors[pid]:
                votes[labels[other]] += w
            if votes:
                best = max(votes.items(), key=lambda kv: kv[1])[0]
                if best != labels[pid]:
                    labels[pid] = best
                    changed = True
        if not changed:
            break

    groups: dict[str, list[str]] = defaultdict(list)
    for pid, label in labels.items():
        groups[label].append(pid)
    clusters = [
        {"id": f"cluster_{i}", "members": sorted(members)}
        for i, (_, members) in enumerate(sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True))
        if len(members) >= 3
    ]
    for node in nodes:
        for c in clusters:
            if node["id"] in c["members"]:
                node["cluster"] = c["id"]
    return clusters


def _insights(nodes: list[dict], edges: list[dict]) -> dict:
    by_id = {n["id"]: n for n in nodes}

    def rel_edges(kinds: set[str]):
        return [e for e in edges if e.get("relationship") in kinds]

    legacy_ids = {n["id"] for n in nodes if n.get("node_type") == "legacy_figure"}

    def subject_edge_other(e) -> str | None:
        # Rival/defender OF THE SUBJECT must come from an edge touching the
        # subject, and never a legacy (deceased-per-sources) figure.
        if e["source"] == "subject":
            other = e["target"]
        elif e["target"] == "subject":
            other = e["source"]
        else:
            return None
        return None if other in legacy_ids else other

    insights: dict = {}
    attacks = sorted(rel_edges({"rival", "attacks", "succession_dispute"}), key=lambda e: e["weight"], reverse=True)
    for e in attacks:
        other = subject_edge_other(e)
        if other:
            insights["top_rival"] = {"who": other, "relationship": e["relationship"], "evidence": e.get("evidence", [])}
            break
    defends = sorted(rel_edges({"ally", "defends"}), key=lambda e: e["weight"], reverse=True)
    for e in defends:
        other = subject_edge_other(e)
        if other:
            insights["top_defender"] = {"who": other, "relationship": e["relationship"], "evidence": e.get("evidence", [])}
            break
    # Person↔person conflicts/alliances are still gold — surface separately.
    conflicts = [
        {"pair": [e["source"], e["target"]], "relationship": e["relationship"], "evidence": e.get("evidence", [])}
        for e in attacks
        if e["source"] != "subject" and e["target"] != "subject"
    ][:3]
    if conflicts:
        insights["notable_conflicts"] = conflicts
    rising = sorted((e for e in edges if e.get("trend", 0) > 0 and e["kind"] == "co_mentioned" and e["source"] != "subject"),
                    key=lambda e: e["trend"], reverse=True)
    if rising:
        e = rising[0]
        insights["rising_connection"] = {"pair": [e["source"], e["target"]], "trend": e["trend"], "weight": e["weight"]}
    # bridge figure: person connected into 2+ clusters
    cluster_links: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        s, t = by_id.get(e["source"]), by_id.get(e["target"])
        if s and t and s.get("cluster") and t.get("cluster") and s["cluster"] != t["cluster"]:
            cluster_links[e["source"]].add(t["cluster"])
            cluster_links[e["target"]].add(s["cluster"])
    if cluster_links:
        bridge = max(cluster_links.items(), key=lambda kv: len(kv[1]))
        insights["bridge_figure"] = {"who": bridge[0], "clusters_connected": len(bridge[1]) + 1}
    legacy = [n["id"] for n in nodes if n.get("node_type") == "legacy_figure"]
    if legacy:
        insights["legacy_figures"] = legacy
    return insights

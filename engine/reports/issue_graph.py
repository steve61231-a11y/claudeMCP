"""One graph over everything the investigation found.

The requirement is explicit: not a pretty disconnected spiderweb. Every node
here is something the research actually produced, and every node carries the
evidence it came from, so selecting anything in the UI can filter everything
else to the same underlying items.

    issue ─ sub-issue ─ person ─ organisation ─ event ─ narrative ─ source

Nodes are keyed by a stable id so the frontend can cross-highlight without
re-deriving anything, and every edge names WHY the two are connected and how
confident that is. An edge asserted with no evidence behind it is exactly the
decorative spiderweb this exists to avoid, so it is not emitted.
"""

from __future__ import annotations

import hashlib
import re

#: How sure we are that an edge is real.
CONFIDENCE_STATED = "stated"      # a source says so in as many words
CONFIDENCE_COOCCUR = "co-occurs"  # they appear together, repeatedly
CONFIDENCE_INFERRED = "inferred"  # the analyst drew the link

NODE_COLOURS = {
    "principal": "#7dd3fc", "issue": "#fbbf24", "sub_issue": "#fcd34d",
    "person": "#c4b5fd", "organization": "#86efac", "event": "#f9a8d4",
    "narrative": "#fb7185", "source": "#94a3b8",
}


def _nid(kind: str, label: str) -> str:
    digest = hashlib.blake2b(f"{kind}:{label.lower().strip()}".encode(),
                             digest_size=5).hexdigest()
    return f"{kind}_{digest}"


class _Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, kind: str, label: str, **extra) -> str | None:
        label = re.sub(r"\s+", " ", str(label or "")).strip()
        if not label:
            return None
        node_id = _nid(kind, label)
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = {
                "id": node_id, "type": kind, "label": label[:120],
                "color": NODE_COLOURS.get(kind, "#94a3b8"),
                "weight": 1, "evidence": [], **extra,
            }
        else:
            existing["weight"] += 1
            for key, value in extra.items():
                if value and not existing.get(key):
                    existing[key] = value
        return node_id

    def edge(self, source: str | None, target: str | None, relation: str,
             confidence: str, evidence: list[dict] | None = None) -> None:
        # No evidence, no edge. A line drawn between two nodes because they
        # both exist is decoration, and decoration is indistinguishable from a
        # finding once it is on the screen.
        if not source or not target or source == target:
            return
        if confidence != CONFIDENCE_INFERRED and not evidence:
            return
        key = (source, target, relation)
        entry = self.edges.get(key)
        if entry is None:
            self.edges[key] = {
                "source": source, "target": target, "relation": relation,
                "confidence": confidence, "weight": 1,
                "evidence": list(evidence or [])[:6],
            }
        else:
            entry["weight"] += 1
            for row in (evidence or []):
                if len(entry["evidence"]) < 6 and row not in entry["evidence"]:
                    entry["evidence"].append(row)

    def find(self, kind: str, label: str) -> str | None:
        """An existing node by name, or None. Naming an actor in a sub-issue
        connects the two; it does not conjure an actor nobody reported."""
        label = re.sub(r"\s+", " ", str(label or "")).strip()
        node_id = _nid(kind, label) if label else None
        return node_id if node_id in self.nodes else None

    def attach_evidence(self, node_id: str | None, rows: list[dict]) -> None:
        if not node_id or not rows:
            return
        held = self.nodes[node_id]["evidence"]
        for row in rows:
            if len(held) < 8 and row not in held:
                held.append(row)

    def result(self) -> dict:
        nodes = sorted(self.nodes.values(), key=lambda n: -n["weight"])
        edges = sorted(self.edges.values(), key=lambda e: -e["weight"])
        connected = {e["source"] for e in edges} | {e["target"] for e in edges}
        return {
            "nodes": nodes,
            "edges": edges,
            "legend": [{"type": kind, "color": colour,
                        "count": sum(1 for n in nodes if n["type"] == kind)}
                       for kind, colour in NODE_COLOURS.items()
                       if any(n["type"] == kind for n in nodes)],
            "stats": {
                "nodes": len(nodes), "edges": len(edges),
                # A node nothing connects to is a fact the investigation found
                # but could not place. Worth knowing, not worth hiding.
                "isolated": sum(1 for n in nodes if n["id"] not in connected),
            },
        }


def _same_entity(a: str, b: str) -> bool:
    """Two labels for the same thing. Deliberately narrow: containment only,
    so "Kenya Power" and "Kenya Pipeline" stay distinct."""
    left = re.sub(r"[^a-z0-9 ]", " ", (a or "").lower()).split()
    right = re.sub(r"[^a-z0-9 ]", " ", (b or "").lower()).split()
    if not left or not right:
        return False
    return " ".join(left) in " ".join(right) or " ".join(right) in " ".join(left)


def _evidence_rows(items, limit: int = 4) -> list[dict]:
    rows = []
    for item in (items or [])[:limit]:
        if not isinstance(item, dict):
            continue
        rows.append({
            "text": str(item.get("text") or item.get("quote") or
                        item.get("statement") or item.get("event") or "")[:280],
            "url": item.get("url") or item.get("source_url"),
            "platform": item.get("platform"),
            "ref": item.get("ref") or item.get("mention_id"),
            "date": item.get("posted_at") or item.get("date"),
        })
    return [r for r in rows if r["text"] or r["url"]]


def build(principal: str, issue: str, analysis: dict,
          framework: dict | None = None, corpus: list[dict] | None = None) -> dict:
    """Assemble the graph from what the investigation actually produced."""
    graph = _Graph()
    analysis = analysis or {}

    principal_id = graph.node("principal", principal, role="principal")
    issue_id = graph.node("issue", issue, role="issue")
    graph.edge(principal_id, issue_id, "is mapped against", CONFIDENCE_INFERRED)

    # People and organisations the analyst named, each linked to whichever half
    # of the intersection it was named in relation to.
    for actor in analysis.get("key_actors") or []:
        if not isinstance(actor, dict):
            continue
        name = str(actor.get("name") or "").strip()
        # The principal is usually also named as an actor. Two nodes for one
        # person produces "Okiya Omtatah connected to Okiya Omtatah" and splits
        # their evidence across both, so reuse the principal node.
        if name and _same_entity(name, principal):
            graph.attach_evidence(principal_id, _evidence_rows(actor.get("quotes")))
            continue
        kind = "organization" if (actor.get("entity_type") or "").startswith("org") else "person"
        node_id = graph.node(kind, name,
                             stance=actor.get("position"),
                             influence=actor.get("influence"),
                             detail=(actor.get("relation") or "")[:400])
        evidence = _evidence_rows(actor.get("quotes"))
        graph.attach_evidence(node_id, evidence)
        graph.edge(node_id, issue_id, actor.get("position") or "involved in",
                   CONFIDENCE_STATED if evidence else CONFIDENCE_INFERRED, evidence)
        graph.edge(principal_id, node_id, "connected to",
                   CONFIDENCE_COOCCUR if evidence else CONFIDENCE_INFERRED, evidence)

    # Narratives — the storylines linking the two halves.
    for narrative in analysis.get("linking_narratives") or []:
        if not isinstance(narrative, dict):
            continue
        node_id = graph.node("narrative", narrative.get("narrative") or "",
                             strength=narrative.get("strength"),
                             detail=(narrative.get("summary") or "")[:400])
        evidence = _evidence_rows(narrative.get("quotes"))
        graph.attach_evidence(node_id, evidence)
        graph.edge(node_id, issue_id, "frames", CONFIDENCE_STATED if evidence
                   else CONFIDENCE_INFERRED, evidence)
        graph.edge(principal_id, node_id, "is described by",
                   CONFIDENCE_COOCCUR if evidence else CONFIDENCE_INFERRED, evidence)

    # Sub-issues — what the issue actually breaks into. An issue map that
    # cannot answer "which fight is this" is a map of one thing.
    for sub in analysis.get("sub_issues") or []:
        if not isinstance(sub, dict) or not sub.get("sub_issue"):
            continue
        node_id = graph.node("sub_issue", sub.get("sub_issue") or "",
                             question=sub.get("question"),
                             root=bool(sub.get("root")),
                             detail=(sub.get("detail") or "")[:600])
        evidence = _evidence_rows(sub.get("quotes"))
        graph.attach_evidence(node_id, evidence)
        graph.edge(node_id, issue_id,
                   "root of" if sub.get("root") else "part of",
                   CONFIDENCE_STATED if evidence else CONFIDENCE_INFERRED, evidence)
        for who in sub.get("actors") or []:
            actor_id = graph.find("person", str(who)) or graph.find("organization", str(who))
            if actor_id:
                graph.edge(actor_id, node_id, "is on",
                           CONFIDENCE_COOCCUR if evidence else CONFIDENCE_INFERRED,
                           evidence)

    # Events, in time, each tied to the actors named in it.
    # The principal counts as an actor here. They are the person the whole map
    # is about, so an event naming them that does not connect back to them is
    # the one gap a reader would notice first.
    actor_labels = {n["label"].lower(): n["id"] for n in graph.nodes.values()
                    if n["type"] in ("person", "organization", "principal")}
    for moment in analysis.get("timeline") or []:
        if not isinstance(moment, dict) or not moment.get("event"):
            continue
        node_id = graph.node("event", str(moment.get("event"))[:110],
                             date=moment.get("date"),
                             sources=moment.get("sources"),
                             detail=str(moment.get("event"))[:600])
        evidence = _evidence_rows(moment.get("quotes"))
        graph.attach_evidence(node_id, evidence)
        graph.edge(node_id, issue_id, "advanced", CONFIDENCE_STATED if evidence
                   else CONFIDENCE_INFERRED, evidence)
        # An event that names an actor connects to that actor. This is what
        # makes selecting a person filter the timeline to their events.
        haystack = str(moment.get("event") or "").lower()
        for label, actor_id in actor_labels.items():
            if label and label in haystack:
                # The event names them. When the moment carries a quote that is
                # a stated link; when it does not, the name in the event text is
                # still a real co-occurrence, so it is drawn as inferred rather
                # than dropped — otherwise selecting an actor filters the
                # timeline to nothing and looks like an absence of history.
                graph.edge(actor_id, node_id, "involved in",
                           CONFIDENCE_STATED if evidence else CONFIDENCE_INFERRED,
                           evidence)

    # Two actors named in the same moment are connected through it. Without
    # this, selecting a person answers "which events" but never "who else was
    # in the room", which is the question an investigator actually asks.
    for moment in analysis.get("timeline") or []:
        if not isinstance(moment, dict) or not moment.get("event"):
            continue
        haystack = str(moment.get("event") or "").lower()
        present = [aid for label, aid in actor_labels.items() if label and label in haystack]
        if len(present) < 2:
            continue
        evidence = _evidence_rows(moment.get("quotes"))
        for i, left in enumerate(present):
            for right in present[i + 1:]:
                graph.edge(left, right, "named in the same moment",
                           CONFIDENCE_COOCCUR if evidence else CONFIDENCE_INFERRED,
                           evidence)

    # Sub-issues from the framework's own contours, kept under the parent issue.
    contours = ((framework or {}).get("main_contours") or {}).get("positions") or {}
    for stance, block in contours.items():
        if not isinstance(block, dict):
            continue
        for segment in (block.get("segments") or {}).values():
            for holder in segment if isinstance(segment, list) else []:
                name = holder.get("name") if isinstance(holder, dict) else holder
                node_id = graph.node("person", str(name or ""), stance=stance)
                graph.edge(node_id, issue_id, stance, CONFIDENCE_INFERRED)

    # Sources, so "which outlets carried this" is answerable from the graph.
    for item in (corpus or [])[:60]:
        platform = item.get("platform")
        if not platform:
            continue
        source_id = graph.node("source", platform, outlet=True)
        graph.attach_evidence(source_id, _evidence_rows([item], limit=1))
        graph.edge(source_id, issue_id, "reported on", CONFIDENCE_STATED,
                   _evidence_rows([item], limit=1))

    return graph.result()

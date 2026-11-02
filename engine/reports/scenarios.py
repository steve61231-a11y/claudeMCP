"""Scenario mapping for the Issue Map — risk drivers, a probability tree and
the three scenarios a client plans against, each standing on the record.

The client's own model of this section (SCENARIO_MAPPING_Example.docx, the
2022 election paper) has three parts: a table of risk drivers (category,
definition, impact, and how the driver reads NOW), a branching tree of
events with a probability on every branch, and three scenarios — most
likely, credible alternative, outlier — each with its factors, outlook and
implications. A human analyst wrote that paper from years of reading; a
model asked for the same thing will produce it fluently from nothing, which
is exactly what this product may not sell.

So the model writes the judgement and the code holds it to the record:

- Every driver's current reading, and every scenario's factors, cite
  findings from the verified record (F), counted patterns (P) or search
  signals (S). A reading or factor that introduces a name, figure or date
  its cited findings lack is removed and declared (`from_facts.
  check_assessment`) — a judgement may reason, it may not smuggle in facts.
  Outlook and implications look forward, so they are held to the findings
  their scenario's factors cite.
- A driver with no checked reading is dropped: a driver with nothing on the
  record saying how it stands now is a textbook heading, not intelligence.
  A scenario with no checked factor is dropped for the same reason.
- The probabilities are the model's judgement and are labelled as such. The
  ARITHMETIC is not: the branches under every node must sum to 100, or the
  tree is withdrawn with the sum it reached; a scenario's probability is
  the product of the branches on its path (summed over the nodes it
  covers), computed here and never taken from the writer; and most likely /
  credible alternative / outlier is the order of those computed figures.
  The example paper itself headlines "45%" over a tree whose branch reads
  40% — a figure in a client's file must be one the page can recompute.
- Too thin a record — fewer than MIN_FINDINGS about the principal, or none
  naming the issue — and nothing is modelled, with the reason, because a
  scenario tree over no evidence is the most persuasive form of invention.
"""

from __future__ import annotations

import re

from engine import llm
from engine.reports import from_facts

MIN_FINDINGS = 3
LEDGER_SIZE = 30
MAX_DRIVERS = 8
MAX_RECOMMENDATIONS = 8
OUTCOMES = ("desired", "opposed", "undetermined")
MAX_NODES = 40
MAX_DEPTH = 6
#: Rounding in the writer's percentages: 33/33/33 is a fair split of three.
SUM_TOLERANCE = 1

TIERS = ("most_likely", "credible_alternative", "outlier")
TIER_LABEL = {"most_likely": "Most likely", "credible_alternative": "Credible alternative",
              "outlier": "Outlier"}
DIRECTIONS = {"raises", "lowers", "mixed", "unclear"}
KINDS = {"event", "outcome", "intervening"}

PROMPT = """You are the senior analyst writing the scenario mapping of an issue map: how {principal} is connected to {issue}, and how that could unfold from here. A client will plan against it.

You are given a numbered ledger. F entries are findings on the verified record (each rests on words a source printed); P entries were counted across the record; S entries are what the public searched for — attention, not fact; D entries are dated steps ahead that a public record states (a sitting on an order paper, a hearing on a court's cause list). That ledger is ALL you know. You may reason from it; you may not add a person, organisation, figure or date it does not contain.

Write three things.

{desired}

1. "drivers": up to {max_drivers} risk drivers — the major variables that decide how this issue goes: a court decision, presidential or executive pressure, a parliamentary committee, a regulator, public pressure, money. For each:
   - "category": one of Political, Electoral, Institutional, Legal, Security, Economic, International, Public opinion, Media
   - "driver": the force, in a few words
   - "definition": how this force works, in general terms (no names, figures or dates)
   - "impact": what it does to the outcome when present (general terms)
   - "reading": how this driver stands NOW, from the ledger — one or two sentences
   - "direction": "raises", "lowers", "mixed" or "unclear" — what it does to the risk to {principal} on this issue now
   - "facts": the ledger ids the reading rests on (at least one)

2. "tree": the branching of what could happen next, as nodes. The first node is the trigger (the next decision point the ledger points to) and has no parent. If there is a D entry, the trigger is the earliest D entry: label it as that step, with its date. Every other node has a "parent" and a "p": the percentage chance of that branch given its parent. The branches under one parent must add up to 100. A node with exactly one child that is not a choice (an intervening step) may leave that child's "p" out. Each node: "id" (n1, n2…), "parent", "label" (a few words), "kind" ("event", "outcome" or "intervening"), "detail" (optional, one sentence), "p". Every END node (a node with no children) also carries "outcome": "desired" if the desired outcome prevails on that path, "opposed" if it does not, "undetermined" if the path does not settle it. At most {max_nodes} nodes and {max_depth} levels.

3. "scenarios": exactly three, the most likely, a credible alternative and an outlier. Each:
   - "title": one line
   - "nodes": the ids of the tree nodes this scenario covers (end points of paths; do not list a node and its ancestor)
   - "factors": sentences on what drives this path, each {{"text", "facts"}} citing ledger ids
   - "outlook": sentences on how it plays out, each {{"text"}}
   - "implications": sentences on what it means for {principal} and for a client, each {{"text"}}

4. "recommendations": up to {max_recs} actions a client pursuing the desired outcome should take, the most important first. Each: "action" (what to do, one line), "who" (whom to engage — only a name the ledger contains, or a role such as "the Attorney General's office" if the ledger names it), "when" (before which decision point in the tree, in words), "rationale" (one or two sentences, why, from the ledger), "facts" (the ledger ids the rationale rests on, at least one).

Do not state a probability for a scenario: it is computed from the tree. The probabilities are your judgement; make them honest — a thin ledger means wide uncertainty, not confident numbers. If the ledger cannot support a credible tree, return "tree": [] and "scenarios": [] and say why in "withheld".

Return JSON: {{"drivers": [...], "tree": [...], "scenarios": [...], "recommendations": [...], "withheld": ""}}"""


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

def ledger_for(connection_findings: list[dict], principal_findings: list[dict],
               patterns: list[dict] | None = None, search: list[dict] | None = None) -> list[dict]:
    """Findings naming both first (they are the connection), then the rest of
    the principal's record — the context a driver's reading needs — then
    counted patterns and search signals."""
    seen, ordered = set(), []
    for f in list(connection_findings or []) + list(principal_findings or []):
        key = f.get("statement")
        if key and key not in seen:
            seen.add(key)
            ordered.append(f)
    base = from_facts.build_ledger(ordered, size=LEDGER_SIZE)
    return base + from_facts.pattern_entries(patterns, base) + from_facts.search_entries(search)


MAX_DATED = 4


def dated_entries(ahead: list[dict] | None) -> list[dict]:
    """D entries: steps ahead a public record states — an order paper's
    sitting, a cause list's hearing — earliest first. The tree's trigger
    should be one of these; without them the writer had nowhere to take a
    real date from, and "the next decision point" was its own guess."""
    rows = sorted((a for a in ahead or [] if a.get("date") and a.get("event")), key=lambda a: a["date"])
    out = []
    for i, a in enumerate(rows[:MAX_DATED], start=1):
        statement = f"{a['date']}: {a['event']} ({a.get('publisher') or 'a public record'})"
        out.append({"id": f"D{i}", "statement": statement, "status": "reported", "independent_sources": 1,
                    "first_seen": None, "dated": True,
                    "sources": [{"quote": a.get("printed") or a["event"], "url": a.get("url"),
                                 "publisher": a.get("publisher"), "ref": None}],
                    "text": " ".join(x for x in (statement, a.get("printed") or "") if x),
                    "step": {k: a.get(k) for k in ("date", "event", "publisher", "url", "record")}})
    return out


# ---------------------------------------------------------------------------
# The tree's arithmetic
# ---------------------------------------------------------------------------

def _pct(value) -> int | None:
    try:
        n = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    return int(round(n)) if 0 <= n <= 100 else None


def validate_tree(nodes: list[dict]) -> tuple[list[dict], str | None]:
    """(nodes with computed `path_p`, None) or ([], the reason the tree
    cannot stand). Every probability on the page is recomputed here."""
    clean: list[dict] = []
    ids = set()
    for raw in nodes or []:
        if not isinstance(raw, dict):
            continue
        nid = str(raw.get("id") or "").strip()
        label = str(raw.get("label") or "").strip()
        if not nid or not label or nid in ids:
            return [], f"node {nid or '?'} has no id or label, or repeats an id"
        ids.add(nid)
        kind = str(raw.get("kind") or "event").strip().lower()
        parent = raw.get("parent")
        parent = str(parent).strip() if parent not in (None, "", "null") else None
        clean.append({"id": nid, "parent": parent, "label": label,
                      "kind": kind if kind in KINDS else "event",
                      "detail": str(raw.get("detail") or "").strip(),
                      "outcome": str(raw.get("outcome") or "").strip().lower() or None,
                      "p": _pct(raw.get("p")) if raw.get("p") not in (None, "") else None})
    if not clean:
        return [], "no tree was written"
    if len(clean) > MAX_NODES:
        return [], f"{len(clean)} nodes, more than the {MAX_NODES} a reader can follow"
    roots = [n for n in clean if n["parent"] is None]
    if len(roots) != 1:
        return [], f"the tree has {len(roots)} starting points; it needs exactly one"
    by_id = {n["id"]: n for n in clean}
    for n in clean:
        if n["parent"] is not None and n["parent"] not in by_id:
            return [], f"node {n['id']} hangs from {n['parent']}, which does not exist"
    children: dict[str, list[dict]] = {}
    for n in clean:
        if n["parent"] is not None:
            children.setdefault(n["parent"], []).append(n)
    for parent, kids in children.items():
        if len(kids) == 1 and kids[0]["p"] is None:
            kids[0]["p"] = 100
            kids[0]["kind"] = "intervening" if kids[0]["kind"] == "event" else kids[0]["kind"]
            continue
        if any(k["p"] is None for k in kids):
            return [], f"a branch under {by_id[parent]['label']} has no probability"
        total = sum(k["p"] for k in kids)
        if abs(total - 100) > SUM_TOLERANCE:
            return [], (f"the branches under “{by_id[parent]['label']}” add up to {total}%, "
                        f"not 100%")

    # Path probability, root first; a cycle never reaches the root.
    order, depth = [roots[0]], {roots[0]["id"]: 0}
    roots[0]["path_p"] = 100.0
    i = 0
    while i < len(order):
        node = order[i]
        for kid in children.get(node["id"], []):
            if kid["id"] in depth:
                return [], f"node {kid['id']} is reached twice"
            depth[kid["id"]] = depth[node["id"]] + 1
            if depth[kid["id"]] > MAX_DEPTH:
                return [], f"the tree is deeper than {MAX_DEPTH} levels"
            kid["path_p"] = node["path_p"] * kid["p"] / 100
            order.append(kid)
        i += 1
    if len(order) != len(clean):
        return [], "part of the tree is not connected to its starting point"
    for n in order:
        n["depth"] = depth[n["id"]]
        n["path_p"] = round(n["path_p"], 1)
    return order, None


def _ancestors(node_id: str, by_id: dict) -> set[str]:
    out, at = set(), by_id.get(node_id, {}).get("parent")
    while at:
        out.add(at)
        at = by_id.get(at, {}).get("parent")
    return out


def scenario_probability(node_ids: list[str], tree: list[dict]) -> tuple[float | None, str | None]:
    """The sum of the path probabilities of the nodes a scenario covers —
    which must exist and must not include a node and its own ancestor,
    because that counts one path twice."""
    by_id = {n["id"]: n for n in tree}
    ids = [i for i in dict.fromkeys(str(x).strip() for x in node_ids or []) if i]
    if not ids:
        return None, "names no node of the tree"
    missing = [i for i in ids if i not in by_id]
    if missing:
        return None, f"names node {missing[0]}, which is not in the tree"
    for i in ids:
        if _ancestors(i, by_id) & set(ids):
            return None, f"counts node {i} and one of its own ancestors"
    return round(sum(by_id[i]["path_p"] for i in ids), 1), None


# ---------------------------------------------------------------------------
# Checking the words
# ---------------------------------------------------------------------------

def _ids(value) -> list[str]:
    return [str(c).strip().upper() for c in (value or []) if str(c).strip()]


def _known(ids: list[str], ledger: list[dict]) -> list[str]:
    have = {e["id"] for e in ledger}
    return [i for i in ids if i in have]


def _general(text: str, ledger: list[dict], principal: str, numbers) -> str | None:
    """A definition or a tree label: held to the whole ledger — no name,
    figure or date the record lacks."""
    return from_facts.check_assessment(text, [e["id"] for e in ledger], ledger, principal, numbers)


_CATEGORY = {c.lower(): c for c in ("Political", "Electoral", "Institutional", "Legal", "Security",
                                    "Economic", "International", "Public opinion", "Media")}


def _category(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return _CATEGORY.get(text.lower()) or (text[:1].upper() + text[1:] if text else "Other")


def check_drivers(raw: list, ledger: list[dict], principal: str,
                  numbers: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("driver") or "").strip()
        reading = str(item.get("reading") or "").strip()
        cited = _known(_ids(item.get("facts")), ledger)
        if not name:
            continue
        if not reading or not cited:
            removed.append({"part": "driver", "sentence": name,
                            "reason": "no reading on the record says how it stands now"})
            continue
        why = from_facts.check_assessment(reading, cited, ledger, principal, numbers)
        if why:
            removed.append({"part": "driver", "sentence": reading, "facts": cited, "reason": why})
            continue
        general = {}
        for field in ("driver", "definition", "impact"):
            text = str(item.get(field) or "").strip()
            why = _general(text, ledger, principal, numbers) if text else None
            if why:
                removed.append({"part": f"driver {field}", "sentence": text, "reason": why,
                                "kept_driver": field != "driver"})
                text = ""
            general[field] = text
        if not general["driver"]:
            continue
        text, citations = from_facts.render([{"text": reading, "facts": cited}], ledger)
        direction = str(item.get("direction") or "").strip().lower()
        strength = from_facts.strength(cited, ledger)
        kept.append({"category": _category(item.get("category")), **general,
                     "reading": text, "reading_citations": citations,
                     "direction": direction if direction in DIRECTIONS else "unclear",
                     "strength": strength, "strength_label": from_facts.STRENGTH[strength],
                     "basis": "verified_facts"})
        if len(kept) >= MAX_DRIVERS:
            break
    return kept, removed


def _sentences(items, ledger, principal, numbers, support_ids, part, removed, need_cites):
    """Checked sentences of one scenario part, rendered with citations."""
    out = []
    for item in items or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cited = _known(_ids(item.get("facts")), ledger)
        if need_cites and not cited:
            removed.append({"part": part, "sentence": text, "reason": "cites nothing on the record"})
            continue
        against = cited or support_ids
        why = from_facts.check_assessment(text, against, ledger, principal, numbers)
        if why:
            removed.append({"part": part, "sentence": text, "facts": against, "reason": why})
            continue
        if cited:
            rendered, citations = from_facts.render([{"text": text, "facts": cited}], ledger)
        else:
            rendered, citations = text, []
        out.append({"text": rendered, "citations": citations, "facts": cited})
    return out


def check_scenarios(raw: list, tree: list[dict], ledger: list[dict], principal: str,
                    numbers: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    kept, removed = [], []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        p, why = scenario_probability(item.get("nodes") or [], tree)
        if why:
            removed.append({"part": "scenario", "sentence": title, "reason": "the scenario " + why})
            continue
        title_why = _general(title, ledger, principal, numbers)
        if title_why:
            removed.append({"part": "scenario", "sentence": title, "reason": title_why})
            continue
        lost: list[dict] = []
        factors = _sentences(item.get("factors"), ledger, principal, numbers, [], "factor", lost, True)
        support = sorted({f for s in factors for f in s["facts"]})
        if not factors:
            removed.extend(lost)
            removed.append({"part": "scenario", "sentence": title,
                            "reason": "no factor under it survived the check against the record"})
            continue
        outlook = _sentences(item.get("outlook"), ledger, principal, numbers, support, "outlook", lost, False)
        implications = _sentences(item.get("implications"), ledger, principal, numbers, support,
                                  "implication", lost, False)
        removed.extend(lost)
        strength = from_facts.strength(support, ledger)
        kept.append({"title": title, "nodes": [str(x).strip() for x in item.get("nodes") or []],
                     "probability": p, "factors": factors, "outlook": outlook,
                     "implications": implications, "strength": strength,
                     "strength_label": from_facts.STRENGTH[strength], "basis": "verified_facts"})
    # The tiers are the order of the computed probabilities, not the writer's.
    kept.sort(key=lambda s: -s["probability"])
    kept = kept[:len(TIERS)]
    for tier, scenario in zip(TIERS, kept):
        scenario["tier"] = tier
        scenario["tier_label"] = TIER_LABEL[tier]
    return kept, removed


def _tier_of_nodes(tree: list[dict], scenarios: list[dict]) -> None:
    """Colour the tree as the paper does: each node takes the tier of the
    most likely scenario whose path passes through it."""
    by_id = {n["id"]: n for n in tree}
    for scenario in reversed(scenarios):
        for nid in scenario["nodes"]:
            for at in {nid} | _ancestors(nid, by_id):
                if at in by_id:
                    by_id[at]["tier"] = scenario["tier"]


def baseline(tree: list[dict]) -> tuple[dict | None, str | None]:
    """The baseline probability of the desired outcome — the sum of the
    path probabilities of the end nodes on which it prevails, opposed and
    undetermined likewise. The founder's note: "we need to show our
    workings with a probability tree" — so the bars are this arithmetic
    over the tree drawn beneath them, never a separate figure. Every end
    node must say which way it falls, or there is no baseline."""
    if not tree:
        return None, "there is no tree"
    parents = {n["parent"] for n in tree if n["parent"]}
    leaves = [n for n in tree if n["id"] not in parents]
    untagged = [n["label"] for n in leaves if n.get("outcome") not in OUTCOMES]
    if untagged:
        return None, f"the end of the path “{untagged[0]}” does not say whether the desired outcome prevails"
    sums = {o: round(sum(n["path_p"] for n in leaves if n["outcome"] == o), 1) for o in OUTCOMES}
    return ({"desired_outcome_prevails": sums["desired"], "opposed_outcome_prevails": sums["opposed"],
             "undetermined": sums["undetermined"],
             "workings": [{"path": n["label"], "probability": n["path_p"], "outcome": n["outcome"]}
                          for n in sorted(leaves, key=lambda n: -n["path_p"])]}, None)


def check_recommendations(raw: list, ledger: list[dict], principal: str,
                          numbers: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Advice is judgement, so it may reason — but whom it names and why
    must stand on the record: `who` and `action` held to the whole ledger,
    the rationale to the findings it cites, and no citation, no advice."""
    kept, removed = [], []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        cited = _known(_ids(item.get("facts")), ledger)
        rationale = str(item.get("rationale") or "").strip()
        if not cited:
            removed.append({"part": "recommendation", "sentence": action, "reason": "cites nothing on the record"})
            continue
        why = (_general(action, ledger, principal, numbers)
               or _general(str(item.get("who") or ""), ledger, principal, numbers)
               or _general(str(item.get("when") or ""), ledger, principal, numbers)
               or (from_facts.check_assessment(rationale, cited, ledger, principal, numbers) if rationale else None))
        if why:
            removed.append({"part": "recommendation", "sentence": action, "facts": cited, "reason": why})
            continue
        text, citations = from_facts.render([{"text": rationale, "facts": cited}], ledger) if rationale else ("", [])
        kept.append({"action": action, "who": str(item.get("who") or "").strip(),
                     # A phrase ("before the hearing"), not a date — named so.
                     "timing": str(item.get("when") or "").strip(), "rationale": text,
                     "rationale_citations": citations, "basis": "verified_facts"})
        if len(kept) >= MAX_RECOMMENDATIONS:
            break
    return kept, removed


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------

def withheld(reason: str) -> dict:
    return {"state": "not_modelled", "reason": reason}


def write(principal: str, issue: str, ledger: list[dict], desired_outcome: str | None = None) -> dict:
    desired = (f"The client's DESIRED OUTCOME, which the tree and the recommendations are oriented to: "
               f"“{desired_outcome}”." if desired_outcome else
               "No desired outcome was given: orient the tree to whether the issue goes for or against "
               f"{principal}, and mark end nodes \"desired\" where it goes for them.")
    reply = llm.call_json_untrusted(
        PROMPT.format(principal=principal, issue=issue, max_drivers=MAX_DRIVERS,
                      max_nodes=MAX_NODES, max_depth=MAX_DEPTH, desired=desired,
                      max_recs=MAX_RECOMMENDATIONS),
        from_facts.ledger_text(ledger),
        expected_keys={"drivers", "tree", "scenarios"},
        max_tokens=llm.budget_for(5000),
        model=llm.strong_model(),
    )
    if not isinstance(reply, dict):
        raise ValueError("reply was not a JSON object")
    return reply


def build(principal: str, issue: str, connection_record: dict | None,
          principal_findings: list[dict] | None = None, search: list[dict] | None = None,
          numbers: set[str] | None = None, desired_outcome: str | None = None,
          ahead: list[dict] | None = None) -> dict:
    """The scenario section, or `withheld(reason)` — which the page shows the
    operator and never shows the reader as "no scenarios"."""
    record = connection_record or {}
    if record.get("state") != "measured":
        return withheld("the record could not be read for this map, so nothing was modelled")
    linked = record.get("findings") or []
    everything = principal_findings if principal_findings is not None else linked
    if not linked:
        return withheld(f"no finding on the record names both {principal} and {issue}; "
                        "a scenario tree over that would be invention")
    if len({f.get('statement') for f in list(linked) + list(everything)}) < MIN_FINDINGS:
        return withheld(f"fewer than {MIN_FINDINGS} findings on the record about {principal}; "
                        "too little to model")
    dated = dated_entries(ahead)
    ledger = ledger_for(linked, everything, record.get("patterns"), search) + dated
    reply = write(principal, issue, ledger, desired_outcome)

    drivers, removed = check_drivers(reply.get("drivers"), ledger, principal, numbers)
    tree, tree_problem = validate_tree(reply.get("tree") or [])
    scenarios: list[dict] = []
    if tree:
        for node in tree:
            for field in ("label", "detail"):
                why = _general(node[field], ledger, principal, numbers) if node[field] else None
                if why and field == "detail":
                    removed.append({"part": "tree detail", "sentence": node["detail"], "reason": why})
                    node["detail"] = ""
                elif why:
                    tree_problem = f"the tree label “{node['label']}” {why}"
                    break
            if tree_problem:
                break
    if tree_problem:
        tree = []
    else:
        scenarios, lost = check_scenarios(reply.get("scenarios"), tree, ledger, principal, numbers)
        removed.extend(lost)
        _tier_of_nodes(tree, scenarios)

    recommendations, lost = check_recommendations(reply.get("recommendations"), ledger, principal, numbers)
    removed.extend(lost)
    base, base_problem = baseline(tree) if tree else (None, None)

    check = {"written": {"drivers": len(reply.get("drivers") or []),
                         "recommendations": len(reply.get("recommendations") or []),
                         "scenarios": len(reply.get("scenarios") or [])},
             "removed": removed, "ledger": len(ledger)}
    if tree_problem:
        check["tree"] = tree_problem
    if base_problem:
        check["baseline"] = base_problem
    if not drivers and not scenarios:
        why = str(reply.get("withheld") or "").strip()
        out = withheld(tree_problem or why or "nothing the writer offered survived the check against the record")
        out["check"] = check
        return out
    return {"state": "modelled", "principal": principal, "issue": issue,
            "drivers": drivers, "tree": tree, "scenarios": scenarios,
            "recommendations": recommendations, "baseline": base,
            "desired_outcome": desired_outcome,
            # The next dated step a public record states — set here, from the
            # record, never by the writer; the tree is read from it.
            "next_dated_step": dated[0]["step"] if dated else None,
            "tree_withheld": tree_problem, "check": check,
            "basis": ("The probabilities are an analyst's judgement on the findings cited, not a "
                      "measurement. Each scenario's figure is the product of the branches on its "
                      "path, recomputed from the tree; every name, figure and date rests on the "
                      "findings cited.")}

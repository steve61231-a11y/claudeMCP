"""Section 4's action plan — written by its own analyst, held to the record.

The founder, on a live map: section 4 "was getting very plain data … the one
for the next steps was just like 3 sentences with a few words. We want
something heavy, something that actually makes sense, something that
actually helps." It was worse than thin: "Recommended messaging" and "Best
engagement approaches" were three fixed sentences, the same on every map
("Prioritise technocrats over political appointees…"), and no analyst wrote
them at all.

So the plan has its own writer, separate from the scenario model, and is
handed everything the map knows: the verified findings (F), the counted
patterns (P), the dated steps ahead a public record states (D), the
stakeholders with where the analysts place them and how much weight they
carry (K), and the scenarios already modelled. It writes:

  * messaging — a core message, the line for each audience, proof points
    resting on findings, and what to avoid saying;
  * plays — four to six concrete campaigns, each with an objective, steps,
    owners, timing against the record's dated steps, the risk and the sign
    it is working;
  * engagement — for each named stakeholder that matters, the specific ask,
    the approach, and why.

Advice is judgement, so it may reason. It may not invent: every name, figure
and date in it must be in what it cites or among the map's stakeholders
(`from_facts.check_assessment`), a proof point must pass `check_sentence`
against its findings, and anything that fails is removed and listed. With
no model, or a thin record, the plan is built from the map's own data —
each stakeholder's position, weight and the analysts' approach, and the
record's next dated step — and says that is what it is.
"""

from __future__ import annotations

import re

from engine import llm
from engine.reports import from_facts, reply_shape

MAX_PLAYS = 6
MAX_ENGAGE = 8
MIN_FINDINGS = 2

PROMPT = """You are the head of strategy at a Kenyan political and business advisory firm, writing the action plan of an issue map: how a client should act on how {principal} is connected to {issue}. Senior people will act on this; it must be specific, practical and sequenced, never generic.

{desired}

You are given a numbered ledger. F entries are verified findings (each rests on words a source printed); P entries were counted across the record; D entries are dated steps ahead that a public record states (a court listing, a parliamentary sitting); K entries are the stakeholders, with where our analysts place them, their weight, and the approach the analysts suggest; C entries are the scenarios already modelled. That ledger is ALL you know. Reason from it as an experienced strategist would; never add a person, organisation, figure or date it does not contain.

Write:

1. "messaging": {{
   "core": the core message in two or three sentences — what the client says, framed to the interests the ledger shows,
   "audiences": up to 4 items {{"audience": a stakeholder name or group from K, "line": the one or two sentences that land with them, "facts": ids}},
   "proof_points": up to 4 items {{"point": a factual sentence stated ONLY from the findings it cites, "facts": ids of F entries}},
   "avoid": up to 3 things not to say or do, each one sentence with its reason, "facts": ids
 }}
2. "plays": {max_plays} or fewer concrete plays, the most important first. Each {{"title": short, "objective": what it achieves, "steps": 2-4 concrete actions in order, "owners": names from K or the client's own team, "when": timing in words, tied to a D entry where one applies, "risk": what could go wrong, "signal": how the client will know it is working, "facts": the ids it rests on, at least one}}.
3. "engagement": up to {max_engage} items, one per stakeholder from K that matters most, {{"name": exactly as in K, "ask": the specific thing to ask of them, "approach": how and through whom, "why": one or two sentences from the ledger, "facts": ids}}.

Be concrete: name the forum, the order of moves, the decision point. A sentence that would fit any issue on any map is a failed sentence. If the ledger is too thin for a credible plan, return empty lists and say why in "withheld".

Return JSON: {{"messaging": {{...}}, "plays": [...], "engagement": [...], "withheld": ""}}"""


def _stakeholders(framework: dict | None) -> list[dict]:
    """Every stakeholder the framework positions, with position and weight."""
    positions = (((framework or {}).get("main_contours") or {}).get("positions") or {})
    out = []
    for position, block in positions.items():
        for people in ((block or {}).get("segments") or {}).values():
            for s in people or []:
                if isinstance(s, dict) and s.get("name"):
                    out.append({"name": s["name"], "position": position, "segment": s.get("segment"),
                                "influence": s.get("influence"), "influence_share": s.get("influence_share"),
                                "approach": s.get("approach"),
                                "on_record": ((s.get("record") or {}).get("count") or 0)})
    out.sort(key=lambda s: -(s.get("influence") or 0))
    return out


def stakeholder_entries(people: list[dict], start: int = 1) -> list[dict]:
    """K entries: a stakeholder as the writer sees them. Their name counts as
    on the record for the checks — it is the map's own list."""
    entries = []
    for i, s in enumerate(people[:12], start=start):
        share = s.get("influence_share")
        bits = [f"{s['name']} — placed {s['position']} by our analysts"]
        if s.get("segment"):
            bits.append(f"segment: {s['segment']}")
        if share is not None:
            bits.append(f"share of influence: {share}%")
        if s.get("on_record"):
            bits.append(f"named with the issue in {s['on_record']} verified statement(s)")
        if s.get("approach"):
            bits.append(f"suggested approach: {s['approach']}")
        statement = "; ".join(bits)
        entries.append({"id": f"K{i}", "statement": statement, "text": statement, "status": "opinion",
                        "independent_sources": 1, "sources": [], "stakeholder": s["name"]})
    return entries


def scenario_entries(scenarios: dict | None, start: int = 1) -> list[dict]:
    if (scenarios or {}).get("state") != "modelled":
        return []
    out = []
    for i, sc in enumerate((scenarios or {}).get("scenarios") or [], start=start):
        statement = f"{sc.get('tier', 'scenario')}: {sc.get('title')} ({sc.get('probability')}%)"
        out.append({"id": f"C{i}", "statement": statement, "text": statement, "status": "opinion",
                    "independent_sources": 1, "sources": []})
    return out


_LEDGER_ID = re.compile(r"\(?\b([FPDKCS])(\d{1,2})\b\)?")
_SENTENCE_START = re.compile(r"(^|[.!?;:]\s+)([A-Z][a-z]+)\b")


def plain(text: str, ledger: list[dict]) -> str:
    """The sentence as a reader should see it: a dated step named by its
    date, any other ledger id dropped ("before the hearing listed in D1"
    becomes "… listed on 2026-12-15")."""
    by_id = {e["id"]: e for e in ledger}

    def sub(m):
        entry = by_id.get(m.group(1) + m.group(2))
        if entry and entry.get("dated") and (entry.get("step") or {}).get("date"):
            return entry["step"]["date"]
        return ""
    return re.sub(r"\s{2,}", " ", _LEDGER_ID.sub(sub, text or "")).strip()


def _check(text: str, cited: list[str], ledger: list[dict], principal: str, numbers) -> str | None:
    """Judgement held to what it cites plus the stakeholder list.

    Advice opens with a verb — "Brief the Finance Committee" — and the name
    finder would read the capitalised verb as part of a name; it is set in
    lower case for the check unless it begins a name the ledger holds."""
    if not text:
        return None
    known = " ".join(e.get("text") or "" for e in ledger)

    def lower(m):
        word = m.group(2)
        return m.group(1) + (word if re.search(rf"\b{re.escape(word)}\b", known) else word.lower())
    ids = list(dict.fromkeys(list(cited) + [e["id"] for e in ledger if e["id"].startswith(("K", "D", "C"))]))
    return from_facts.check_assessment(_SENTENCE_START.sub(lower, text), ids, ledger, principal, numbers)


def _known(ids, ledger) -> list[str]:
    have = {e["id"] for e in ledger}
    return [i.upper() for i in reply_shape.idents(ids) if i.upper() in have]


def check(reply: dict, ledger: list[dict], principal: str, numbers=None) -> tuple[dict, list[dict]]:
    """The plan with every unsupported sentence removed, and the removals."""
    removed: list[dict] = []
    names = {e["stakeholder"] for e in ledger if e.get("stakeholder")}

    def keep(text, cited, part) -> str:
        text = plain(text, ledger)
        why = _check(text, cited, ledger, principal, numbers)
        if why:
            removed.append({"part": part, "sentence": text, "reason": why})
            return ""
        return text

    m = reply_shape.obj(reply.get("messaging"))
    messaging = {"core": keep(reply_shape.text(m.get("core")), [], "core message"),
                 "audiences": [], "proof_points": [], "avoid": []}
    for a in reply_shape.dicts(m.get("audiences"))[:4]:
        cited = _known(a.get("facts"), ledger)
        line = keep(reply_shape.text(a.get("line")), cited, "audience line")
        if line:
            messaging["audiences"].append({"audience": reply_shape.text(a.get("audience")), "line": line,
                                           "facts": cited})
    for p in reply_shape.dicts(m.get("proof_points"))[:4]:
        cited = [c for c in _known(p.get("facts"), ledger) if c.startswith(("F", "P"))]
        point = reply_shape.text(p.get("point"))
        why = "cites no finding" if not cited else from_facts.check_sentence(point, cited, ledger, principal, numbers)
        if why:
            removed.append({"part": "proof point", "sentence": point, "reason": why})
            continue
        text, citations = from_facts.render([{"text": point, "facts": cited}], ledger)
        messaging["proof_points"].append({"point": point, "cited": text, "citations": citations, "facts": cited})
    for a in reply_shape.dicts(m.get("avoid"))[:3]:
        text = keep(reply_shape.text(a.get("text") or a.get("point") or a.get("avoid")),
                    _known(a.get("facts"), ledger), "avoid")
        if text:
            messaging["avoid"].append(text)
    for a in reply_shape.texts(m.get("avoid"))[:3]:
        text = keep(a, [], "avoid")
        if text:
            messaging["avoid"].append(text)

    plays = []
    for p in reply_shape.dicts(reply.get("plays"))[:MAX_PLAYS]:
        title = reply_shape.text(p.get("title"))
        cited = _known(p.get("facts"), ledger)
        if not title:
            continue
        if not cited:
            removed.append({"part": "play", "sentence": title, "reason": "cites nothing on the record"})
            continue
        fields = {k: keep(reply_shape.text(p.get(k)), cited, f"play {k}")
                  for k in ("title", "objective", "when", "risk", "signal")}
        if not fields["title"]:
            continue
        steps = [s for s in (keep(t, cited, "play step") for t in reply_shape.texts(p.get("steps"))[:4]) if s]
        owners = [o for o in reply_shape.texts(p.get("owners"))[:4]
                  if o in names or not _check(o, cited, ledger, principal, numbers)]
        plays.append({**fields, "steps": steps, "owners": owners, "facts": cited})

    engagement = []
    for e in reply_shape.dicts(reply.get("engagement"))[:MAX_ENGAGE]:
        name = reply_shape.text(e.get("name"))
        if name not in names:
            if name:
                removed.append({"part": "engagement", "sentence": name, "reason": "not a stakeholder on this map"})
            continue
        cited = _known(e.get("facts"), ledger)
        engagement.append({"name": name, "ask": keep(reply_shape.text(e.get("ask")), cited, "ask"),
                           "approach": keep(reply_shape.text(e.get("approach")), cited, "approach"),
                           "why": keep(reply_shape.text(e.get("why")), cited, "why"), "facts": cited})
    return {"messaging": messaging, "plays": plays, "engagement": engagement}, removed


def from_the_map(people: list[dict], desired_outcome: str | None, ahead: list[dict] | None) -> dict:
    """The plan with no model: each stakeholder's position, weight and the
    analysts' approach, ordered by weight, and the record's next dated step.
    Plain, but specific to this map — never the same three sentences."""
    nxt = sorted((a for a in ahead or [] if a.get("date")), key=lambda a: a["date"])
    when = f"before {nxt[0]['date']} ({nxt[0].get('event')})" if nxt else "now"
    engagement = []
    verbs = {"for": "Secure and coordinate", "against": "Address the objections of", "neutral": "Win over"}
    for s in people[:MAX_ENGAGE]:
        share = f" (share of influence {s['influence_share']}%)" if s.get("influence_share") is not None else ""
        engagement.append({"name": s["name"],
                           "ask": f"{verbs.get(s['position'], 'Engage')} {s['name']}{share}",
                           "approach": s.get("approach") or "", "why": "", "facts": []})
    champions = [s["name"] for s in people if s["position"] == "for"][:3]
    swing = [s["name"] for s in people if s["position"] == "neutral"][:3]
    plays = []
    if champions:
        plays.append({"title": "Consolidate the champions", "objective": "one coordinated voice before the next decision point",
                      "steps": [f"Brief {n}" for n in champions], "owners": champions, "when": when,
                      "risk": "", "signal": "", "facts": []})
    if swing:
        plays.append({"title": "Move the highest-weight neutrals", "objective": "shift the balance the baseline measures",
                      "steps": [f"Engage {n}" for n in swing], "owners": swing, "when": when,
                      "risk": "", "signal": "", "facts": []})
    core = (f"Every move is aimed at: {desired_outcome}." if desired_outcome else "")
    return {"messaging": {"core": core, "audiences": [], "proof_points": [], "avoid": []},
            "plays": plays, "engagement": engagement}


def build(principal: str, issue: str, framework: dict | None, connection_record: dict | None,
          principal_findings: list[dict] | None, scenarios: dict | None,
          desired_outcome: str | None = None, ahead: list[dict] | None = None,
          numbers: set[str] | None = None) -> dict:
    """{"state": "written"|"from_the_map", "plan", "check"|"reason"}."""
    from engine.reports import scenarios as scenarios_mod

    people = _stakeholders(framework)
    record = connection_record or {}
    findings = list(record.get("findings") or [])
    base = scenarios_mod.ledger_for(findings, principal_findings or findings, record.get("patterns"))
    dated = scenarios_mod.dated_entries(ahead)
    ledger = base + dated + stakeholder_entries(people) + scenario_entries(scenarios)
    usable = sum(1 for e in base if e["id"].startswith("F"))
    fallback = {"state": "from_the_map", "plan": from_the_map(people, desired_outcome, ahead)}
    if usable < MIN_FINDINGS or not people:
        fallback["reason"] = (f"{usable} verified finding(s) and {len(people)} stakeholder(s) — too little for "
                              "a written plan; built from the map's own positions and dates")
        return fallback
    desired = (f"The client's DESIRED OUTCOME: “{desired_outcome}”. Every play serves it."
               if desired_outcome else
               f"No desired outcome was given: plan for a client who needs {principal}'s position on "
               f"{issue} to hold or improve.")
    try:
        reply = llm.call_json_untrusted(
            PROMPT.format(principal=principal, issue=issue, desired=desired,
                          max_plays=MAX_PLAYS, max_engage=MAX_ENGAGE),
            from_facts.ledger_text(ledger),
            expected_keys={"plays"},
            max_tokens=llm.budget_for(4500),
            model=llm.strong_model(),
        )
        if not isinstance(reply, dict):
            raise ValueError("the reply was not a JSON object")
    except Exception as exc:  # noqa: BLE001 — the plan falls back to the map's own data
        fallback["reason"] = f"the plan writer failed: {type(exc).__name__}: {exc}"[:240]
        return fallback
    plan, removed = check(reply, ledger, principal, numbers)
    if not plan["plays"] and not plan["engagement"]:
        why = reply_shape.text(reply.get("withheld"))
        fallback["reason"] = why or "nothing the writer offered survived the check against the record"
        fallback["check"] = {"removed": removed}
        return fallback
    return {"state": "written", "plan": plan,
            "check": {"removed": removed, "ledger": len(ledger)},
            "basis": ("Advice is our judgement. Every name, figure and date in it is on the map's record or "
                      "among its stakeholders; proof points are checked against the findings they cite.")}

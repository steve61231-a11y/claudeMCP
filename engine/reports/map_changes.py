"""What changed since the last map of the same pair — counted, with no model.

Phase 5 of docs/ISSUE_MAP_FOUNDATION.md. The corpus compounds and the
records are re-read on every map, so a client who maps the same person and
issue again wants the difference first: a new court case or ruling, new
verified statements linking them, a hearing that appeared or moved, a
stakeholder newly on the record. Every finished map is stored
(`api_server._store_issue_map`), so the previous one is there to compare.

Everything here is set arithmetic over the two payloads — what the earlier
map held and what this one holds. A first map says it is the first; a
comparison is never invented against nothing. Items that left are counted,
not hidden, because a ruling that no longer appears is itself a finding
(or a source that could not be read this time — which the record sections
already say).
"""

from __future__ import annotations

MAX_LISTED = 5


def _cases(payload: dict) -> dict[str, dict]:
    out = {}
    for p in ((payload or {}).get("connection_paths") or {}).get("paths") or []:
        if p.get("kind") != "court":
            continue
        step = (p.get("steps") or [{}])[0]
        key = p.get("case") or step.get("to")
        if key:
            out[str(key)] = {"case": step.get("to"), "relation": step.get("relation"),
                             "rulings": len(p.get("rulings") or []),
                             "url": (step.get("record") or {}).get("url")}
    return out


def _statements(payload: dict) -> dict[str, dict]:
    cr = (payload or {}).get("connection_record") or {}
    return {f.get("statement"): f for f in cr.get("findings") or [] if f.get("statement")}


def _steps(payload: dict) -> dict[str, dict]:
    """Dated steps ahead, keyed by what they are (so a moved date is a change
    of date, not one step gone and another come)."""
    rows = list(((payload or {}).get("connection_paths") or {}).get("next") or [])
    rows += list((payload or {}).get("upcoming") or [])
    out = {}
    for r in rows:
        event = " ".join(str(r.get("event") or "").split())[:140]
        if event and r.get("date"):
            out.setdefault(event.lower(), {"event": event, "date": r["date"], "publisher": r.get("publisher"),
                                           "url": r.get("url")})
    return out


def _on_record_stakeholders(payload: dict) -> set[str]:
    positions = (((payload or {}).get("issue_framework") or {}).get("main_contours") or {}).get("positions") or {}
    names = set()
    for block in positions.values():
        for people in ((block or {}).get("segments") or {}).values():
            for s in people or []:
                if isinstance(s, dict) and (s.get("record") or {}).get("count"):
                    names.add(s.get("name"))
    return names


def compare(previous: dict | None, current: dict) -> dict:
    """The difference, or {"state": "first_map"}."""
    if not previous:
        return {"state": "first_map"}
    before_cases, now_cases = _cases(previous), _cases(current)
    new_cases = [now_cases[k] for k in now_cases if k not in before_cases]
    new_rulings = [dict(now_cases[k], before=before_cases[k]["rulings"]) for k in now_cases
                   if k in before_cases and now_cases[k]["rulings"] > before_cases[k]["rulings"]]
    before_st, now_st = _statements(previous), _statements(current)
    new_statements = [now_st[s] for s in now_st if s not in before_st]
    before_steps, now_steps = _steps(previous), _steps(current)
    new_steps = [now_steps[k] for k in now_steps if k not in before_steps]
    moved = [dict(now_steps[k], was=before_steps[k]["date"]) for k in now_steps
             if k in before_steps and now_steps[k]["date"] != before_steps[k]["date"]]
    before_people, now_people = _on_record_stakeholders(previous), _on_record_stakeholders(current)
    counts = {"new_cases": len(new_cases), "cases_no_longer_found": len(set(before_cases) - set(now_cases)),
              "new_rulings": len(new_rulings), "new_statements": len(new_statements),
              "statements_no_longer_found": len(set(before_st) - set(now_st)),
              "new_dated_steps": len(new_steps), "moved_dates": len(moved),
              "stakeholders_newly_on_record": len(now_people - before_people)}
    return {"state": "compared", "previous_generated": (previous.get("generated_at") or previous.get("generated")),
            "previous_window": previous.get("window"), "counts": counts,
            "new_cases": new_cases[:MAX_LISTED], "new_rulings": new_rulings[:MAX_LISTED],
            "new_statements": [{"statement": f.get("statement"), "status": f.get("status"),
                                "url": ((f.get("sources") or [{}])[0]).get("url"),
                                "publisher": ((f.get("sources") or [{}])[0]).get("publisher")}
                               for f in new_statements[:MAX_LISTED]],
            "new_dated_steps": new_steps[:MAX_LISTED], "moved_dates": moved[:MAX_LISTED],
            "stakeholders_newly_on_record": sorted(n for n in now_people - before_people if n)[:MAX_LISTED],
            "lead": lead(counts)}


def lead(counts: dict) -> str:
    parts = []
    for key, one, many in (("new_cases", "new court case", "new court cases"),
                           ("new_rulings", "case with a new ruling", "cases with new rulings"),
                           ("new_statements", "new verified statement linking them",
                            "new verified statements linking them"),
                           ("new_dated_steps", "new dated step ahead", "new dated steps ahead"),
                           ("moved_dates", "date moved", "dates moved"),
                           ("stakeholders_newly_on_record", "stakeholder newly on the record",
                            "stakeholders newly on the record")):
        n = counts.get(key) or 0
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return "Nothing new on the record since the last map of this pair."
    return "Since the last map: " + (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]) + "."

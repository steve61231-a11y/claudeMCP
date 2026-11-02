"""The connection as a path of evidence — typed edges from the records, no model.

docs/ISSUE_MAP_FOUNDATION.md, section 2 and Phase 2. Every other tool
answers "how is X connected to Y?" with prose; the map answers with the
records that tie them, each step typed and sourced:

    Ministry of Health ── named as respondent in ──▶ Petition E078 of 2025 (ELRC, 10 Jul 2025)
    Petition E078 of 2025 ── concerns ──▶ SHA        (the card's own words)

A step exists only where a record STATES the role: the court card's title
says who brought a case and who answered it; the Hansard prints who spoke;
the World Bank records who borrows and who carries a project out. Nothing
is inferred — the roles are read from the record's own structure, and the
record already had to name the issue to be kept (`issue_map.acquire_records`).
A co-mention is never a step: two names in one article are the fact store's
question (`evidence/connection.py`), and are counted there.

Where the record names the principal in a role only through the office that
stands for it in court — the Attorney General or a Cabinet Secretary for the
national government, the Cabinet Secretary for a Ministry — the step says
so, as printed, with the basis in words.

When no record ties them, the lead says which absence it is: records on
the issue were read and none names the principal in a role; none on the
issue was found; or the records could not be read (`state`).
"""

from __future__ import annotations

import re

#: What each role is called on the page.
RELATIONS = {
    "brought": "brought the case",
    "answered": "named as respondent in",
    "interested": "interested party in",
    "spoke": "spoke on it in the National Assembly",
    "implementing_agency": "implementing agency of",
    "borrower": "borrower under",
}

#: The offices that stand for the national government in court and in a
#: lender's record. Read as the government only for a principal that IS
#: the national government, and the basis is said.
_NATIONAL_GOVERNMENT_PARTY = re.compile(
    r"\b(?:Attorney General|Cabinet Secretary|Principal Secretary|National Treasury|National Executive|"
    r"Republic of Kenya|Government of Kenya|National Government)\b|^Republic\b", re.I)
_IS_NATIONAL_GOVERNMENT = re.compile(
    r"^(?:the\s+)?(?:(?:Kenya|Kenyan|National)\s+Government|Government(?:\s+of\s+Kenya)?|GoK)$", re.I)
_MINISTRY = re.compile(r"^(?:the\s+)?Ministry\s+of\s+(.+)$", re.I)
_FILLER = frozenset("of the and for in on a an".split())

MAX_PATHS = 12


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", re.sub(r"[’'‘`]", "", (text or "").lower())))


def _names(text: str, identity: str) -> bool:
    n = _norm(identity)
    return bool(n) and f" {n} " in f" {_norm(text)} "


def holder(role_text: str, principal: str, identities: list[str]) -> str | None:
    """How the principal is named in a role, or None. A whole-word match on
    one of its names; else, for the national government or a Ministry, the
    office that stands for it — returned as a basis the page prints."""
    if any(_names(role_text, i) for i in identities or [principal] if i):
        return "named"
    if _IS_NATIONAL_GOVERNMENT.match((principal or "").strip()) and _NATIONAL_GOVERNMENT_PARTY.search(role_text):
        return "the national government's officer"
    m = _MINISTRY.match((principal or "").strip())
    if m and re.search(r"\b(?:Cabinet|Principal) Secretary\b|\bMinistry\b", role_text, re.I):
        subject = [w for w in _norm(m.group(1)).split() if w not in _FILLER]
        if subject and all(f" {w} " in f" {_norm(role_text)} " for w in subject):
            return "the Ministry's Cabinet or Principal Secretary"
    return None


def _payload(item: dict) -> dict:
    return item.get("raw_payload") or {}


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.date().isoformat() if hasattr(value, "date") else str(value)[:10]


def _case_label(raw: dict) -> str:
    """The case as a reader cites it: its number, else the ruling's
    citation, else the opening of its title — never a whole title."""
    from engine.ingestion import kenya_law_records

    key = kenya_law_records.case_key(raw.get("case_number"))
    return key or raw.get("case_number") or raw.get("citation") or _excerpt(raw.get("title"), 80) or "a court record"


def _court_steps(item: dict, principal: str, identities: list[str], issue: str) -> list[dict]:
    raw = _payload(item)
    case = _case_label(raw)
    record = {"kind": item.get("source_type") or "judgment", "id": case, "title": raw.get("title"),
              "citation": raw.get("citation"),
              "court": raw.get("court") or item.get("author_handle"), "date": _iso(item.get("posted_at")),
              "url": raw.get("url") or item.get("source_url")}
    out = []
    for role, parties in (raw.get("sides") or {}).items():
        for party in parties or []:
            basis = holder(party, principal, identities)
            if basis:
                out.append({"steps": [
                    {"from": principal, "relation": RELATIONS[role], "to": case, "as_printed": party,
                     "basis": basis, "record": record},
                    {"from": case, "relation": "concerns", "to": issue, "record": record,
                     "words": _excerpt(item.get("text"))}],
                    "kind": "court", "role": role, "date": record["date"],
                    "case": raw.get("case_key") or raw.get("url") or case,
                    # The case's dates in the court's own cause lists.
                    "listings": raw.get("listings") or []})
                break
    return out


def _one_per_case(paths: list[dict]) -> list[dict]:
    """A case is one step however many rulings it has; its rulings are
    listed under it, newest first, each opening its own record."""
    out: list[dict] = []
    by_case: dict[str, dict] = {}
    for p in sorted(paths, key=lambda p: p.get("date") or "", reverse=True):
        if p["kind"] != "court":
            out.append(p)
            continue
        ruling = {k: p["steps"][0]["record"].get(k) for k in ("date", "url", "citation", "court")}
        held = by_case.get(p["case"])
        if held:
            held["rulings"].append(ruling)
            continue
        p["rulings"] = [ruling]
        by_case[p["case"]] = p
        out.append(p)
    return out


def _speech_steps(item: dict, principal: str, identities: list[str], issue: str) -> list[dict]:
    raw = _payload(item)
    speaker = item.get("author_handle") or ""
    basis = holder(speaker, principal, identities)
    if not basis:
        return []
    sitting = raw.get("sitting") or _iso(item.get("posted_at"))
    record = {"kind": "hansard", "id": f"Hansard, {sitting}", "business": raw.get("business"),
              "date": sitting, "url": raw.get("url") or item.get("source_url"), "court": None}
    return [{"steps": [{"from": principal, "relation": RELATIONS["spoke"], "to": issue, "as_printed": speaker,
                        "basis": basis, "record": record, "words": _excerpt(item.get("text"))}],
             "kind": "parliament", "role": "spoke", "date": sitting}]


def _lender_steps(item: dict, principal: str, identities: list[str], issue: str) -> list[dict]:
    raw = _payload(item)
    if not raw.get("project_id"):
        return []
    record = {"kind": "lender", "id": f"World Bank {raw['project_id']}", "title": raw.get("project"),
              "date": _iso(item.get("posted_at")), "url": raw.get("url"), "court": None}
    for role in ("implementing_agency", "borrower"):
        printed = raw.get(role)
        basis = holder(printed or "", principal, identities) if printed else None
        if basis:
            return [{"steps": [
                {"from": principal, "relation": RELATIONS[role], "to": record["id"], "as_printed": printed,
                 "basis": basis, "record": record},
                {"from": record["id"], "relation": "concerns", "to": issue, "record": record,
                 "words": _excerpt(item.get("text"))}],
                "kind": "lender", "role": role, "date": record["date"]}]
    return []


def _excerpt(text, most: int = 320) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= most else text[:most].rsplit(" ", 1)[0] + " …"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def lead(counts: dict, reach: dict, principal: str) -> str:
    """The section's first line, counted from `counts` and `reach`."""
    parts = []
    if counts.get("court"):
        rulings = counts.get("rulings") or counts["court"]
        parts.append(f"is a party to {_plural(counts['court'], 'court case')} on it"
                     + (f" ({rulings} rulings)" if rulings > counts["court"] else ""))
    if counts.get("parliament"):
        parts.append(f"spoke on it in Parliament {_plural(counts['parliament'], 'time')}")
    if counts.get("lender"):
        parts.append(f"holds a role in {_plural(counts['lender'], 'World Bank project')} on it")
    if parts:
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        return f"On the record, {principal} {joined}."
    read = [_plural(reach.get("court", 0), "court record"), _plural(reach.get("parliament", 0), "speech", "speeches"),
            _plural(reach.get("lender", 0), "lender record")]
    if sum(reach.get(k, 0) for k in ("court", "parliament", "lender")):
        return (f"No record read names {principal} in a role on this issue: {read[0]}, {read[1]} and "
                f"{read[2]} on it were read, and none names them as a party, speaker or agency.")
    return "No court record, Hansard speech or lender record on this issue was found in the sources read."


def build(records: list[dict], principal: str, identities: list[str], issue: str,
          ahead: list[dict] | None = None, sources: dict | None = None) -> dict:
    """The evidenced paths from principal to issue over this map's records.

    `records` are the record items the map read (corpus-shaped, with their
    `raw_payload`); `sources` is the acquisition's records report, so a
    source that failed is said rather than read as an absence."""
    sources = sources or {}
    if sources.get("skipped"):
        return {"state": "not_measured", "reason": sources["skipped"]}
    failed = [name for name in ("kenya_law", "parliament", "world_bank")
              if isinstance(sources.get(name), dict) and sources[name].get("error")]
    paths: list[dict] = []
    reach = {"court": 0, "parliament": 0, "lender": 0}
    for item in records or []:
        kind = item.get("source_type")
        raw = _payload(item)
        if raw.get("source") == "kenya_law_search":
            reach["court"] += 1
            paths += _court_steps(item, principal, identities, issue)
        elif kind == "hansard":
            reach["parliament"] += 1
            paths += _speech_steps(item, principal, identities, issue)
        elif raw.get("source", "").startswith("world_bank"):
            reach["lender"] += 1
            paths += _lender_steps(item, principal, identities, issue)
    if not any(reach.values()) and len(failed) == 3:
        return {"state": "not_measured", "reason": "every record source failed: " + ", ".join(failed)}
    paths = _one_per_case(paths)
    paths.sort(key=lambda p: p.get("date") or "", reverse=True)
    counts = {k: sum(1 for p in paths if p["kind"] == k) for k in ("court", "parliament", "lender")}
    counts["rulings"] = sum(len(p.get("rulings") or []) for p in paths if p["kind"] == "court")
    next_steps = [a for a in (ahead or []) if a.get("date")][:3]
    return {"state": "measured", "principal": principal, "issue": issue, "paths": paths[:MAX_PATHS],
            "paths_total": len(paths), "counts": counts, "reach": reach, "next": next_steps,
            "sources_failed": failed, "lead": lead(counts, reach, principal)}

"""Who is involved in the issue, on the record — each stakeholder's verified statements.

Phase 3 of docs/ISSUE_MAP_FOUNDATION.md: involvement written from the
record. The map's stakeholders — who is for, against, neutral — are the
analysts' reading of the coverage, and they rendered as though they were
established. A client deciding whom to engage needs to know which of them
the record actually places in the issue, and in what words.

So each stakeholder carries the verified findings (the fact store, every
statement resting on words a source printed) whose statement names BOTH
that stakeholder and the issue. The position itself stays the analysts' and
is labelled so: deciding "for" or "against" from a sentence is a judgement,
and no code here makes it. What the record adds is the involvement — named
in N statements on the issue, here are the strongest — or its absence,
which is said rather than left blank.

Names are matched whole: an institution only by its full name, a person by
the full name or, for a surname of four letters or more, the surname alone
("Ruto told the rally"), because Kenyan reporting names people that way.
"""

from __future__ import annotations

import re

from engine.evidence import facts

MAX_PER_STAKEHOLDER = 3
_INSTITUTIONAL = re.compile(
    r"\b(?:Ministry|Authority|Commission|Council|Board|Fund|Service|Agency|Court|Assembly|Parliament|Senate|"
    r"Treasury|Government|County|Party|Union|Association|Society|Bank|Limited|Ltd|PLC|Company|Corporation|"
    r"Institute|University|Office|Department|Committee|Coalition|Alliance|Movement|Republic|Kenya)\b")


def _norm(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", re.sub(r"[’'‘`]", " ", (text or "").lower()))) + " "


def name_forms(name: str) -> list[str]:
    """How a source may name this stakeholder."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    forms = [name] if name else []
    parts = name.split()
    if len(parts) >= 2 and not _INSTITUTIONAL.search(name) and all(p[:1].isupper() for p in parts):
        surname = parts[-1]
        if len(surname) >= 4:
            forms.append(surname)
    return forms


def names(statement: str, name: str) -> bool:
    folded = _norm(statement)
    return any(_norm(form) in folded for form in name_forms(name) if form.strip())


def issue_findings(db, subject, corpus: list[dict], about) -> list[dict]:
    """Every verified finding, about anyone, from this map's sources whose
    statement names the issue — corroborated, opinions left out, held to
    the identity rule as the principal's record is."""
    from engine.db.models import Fact
    from engine.reports import outlets, protected_terms

    by_key = {facts.source_key(item): item for item in corpus or [] if facts.source_key(item)}
    if not by_key or subject is None:
        return []
    rows = db.query(Fact).filter(Fact.politician_id == subject.id, Fact.source_key.in_(list(by_key)),
                                 Fact.kind != "opinion").all()
    listed = []
    for fact in rows:
        item = by_key[fact.source_key]
        if not about(fact.statement or ""):
            continue
        if protected_terms.introduced(fact.statement, item.get("text") or ""):
            continue
        listed.append({"statement": fact.statement, "quote": fact.quote, "kind": fact.kind,
                       "ref": str(item.get("id", ""))[:8], "status": fact.status,
                       "publisher": outlets.publisher(item), "source_type": item.get("source_type"),
                       "url": item.get("source_url"), "source_text": item.get("text"),
                       "published_at": fact.published_at.date().isoformat() if fact.published_at else None})
    found = facts.corroborate(listed)
    for finding in found:
        for source in finding.get("sources") or []:
            source.pop("source_text", None)
    return found


def _view(finding: dict) -> dict:
    source = (finding.get("sources") or [{}])[0]
    return {"statement": finding.get("statement"), "status": finding.get("status"),
            "independent_sources": finding.get("independent_sources") or 1,
            "publisher": source.get("publisher"), "url": source.get("url"), "quote": source.get("quote"),
            "date": finding.get("first_seen") or source.get("published_at")}


def annotate(framework: dict, findings: list[dict] | None, state: str = "measured") -> dict:
    """Attach `record` to every stakeholder in the framework's positions and
    return the count, for the section's grade. With the record unread, each
    stakeholder says so — never "named in 0 statements"."""
    positions = ((framework or {}).get("main_contours") or {}).get("positions") or {}
    total = with_record = 0
    for block in positions.values():
        for people in ((block or {}).get("segments") or {}).values():
            for s in people or []:
                if not isinstance(s, dict) or not s.get("name"):
                    continue
                total += 1
                if state != "measured":
                    s["record"] = {"state": "not_measured"}
                    continue
                mine = [f for f in findings or [] if names(f.get("statement") or "", s["name"])]
                mine.sort(key=lambda f: -int(f.get("independent_sources") or 1))
                s["record"] = {"state": "measured", "count": len(mine),
                               "findings": [_view(f) for f in mine[:MAX_PER_STAKEHOLDER]]}
                with_record += bool(mine)
    summary = {"state": state, "stakeholders": total, "on_record": with_record}
    if framework and framework.get("main_contours") is not None:
        framework["main_contours"]["record"] = summary
    return summary

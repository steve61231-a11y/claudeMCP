"""The connection, on the record — what the Issue Map is for, with no model writing it.

An Issue Map answers one question: how is this person connected to this
issue? Its answer was a verdict a model wrote over a digest of the corpus,
and the founder's standard is that "if we say this was said, or Martha Karua
did this or that, it has to be legit data". A connection is exactly the kind
of claim that standard is about: printed in a client's file, "X is tied to
the IMF loan" is an allegation unless something on the record says it.

So the map's first section is built from the verified fact store: every
finding about the principal whose statement ALSO names the issue, with the
words its sources printed, independent origins counted, allegations kept as
allegations, dated by first publication, and the patterns counted across
them. Its lead line is arithmetic. When nothing on the record names both, it
says so — "not established on the record" is the honest answer to a client
who asked whether a connection exists, and it must never be mistaken for a
failure to look (`state` tells the two apart).
"""

from __future__ import annotations

import re

from engine.evidence import facts, patterns

#: Words that name no particular issue. "Odious debt case" is about odious
#: debt; the word "case" alone would tie the principal to every court story.
_GENERIC = {"case", "issue", "matter", "story", "affair", "deal", "plan", "policy", "project",
            "programme", "program", "bill", "act", "scandal", "row", "saga", "the", "and", "for",
            "with", "from", "kenya", "kenyan", "county", "national", "government"}


def issue_phrases(issue: str, aliases: list[str] | None = None) -> list[str]:
    return [p for p in [issue, *(aliases or [])] if str(p or "").strip()]


def about_issue(statement: str, issue: str, aliases: list[str] | None = None) -> bool:
    """Whether a statement names the issue: its exact phrase, an alias, or —
    for a phrase of several words — every distinctive word in it. A short
    name ("IMF") must match as a whole word, never inside another."""
    folded = facts._fold(statement)
    words = set(re.findall(r"[a-z0-9]+", folded))
    for phrase in issue_phrases(issue, aliases):
        p = facts._fold(phrase).strip()
        parts = re.findall(r"[a-z0-9]+", p)
        if not parts:
            continue
        if len(parts) == 1:
            if parts[0] in words:
                return True
            continue
        if p in folded:
            return True
        distinctive = [w for w in parts if len(w) >= 4 and w not in _GENERIC]
        if distinctive and all(w in words for w in distinctive):
            return True
    return False


def build(findings: list[dict], principal: str, issue: str,
          aliases: list[str] | None = None, most: int = 25) -> dict:
    """The connection section from the principal's findings (already the
    principal's — `record_for` keeps only statements naming them)."""
    from engine.reports import from_facts

    linked = [f for f in findings or [] if about_issue(f.get("statement") or "", issue, aliases)]
    established = [f for f in linked if f.get("status") not in ("alleged", "unresolved")]
    alleged = [f for f in linked if f.get("status") in ("alleged", "unresolved")]
    corroborated = [f for f in established if int(f.get("independent_sources") or 1) >= 2]
    primary = [f for f in linked if any(s.get("source_type") in facts.PRIMARY_TYPES
                                        for s in f.get("sources") or [])]
    publishers = {s.get("publisher") for f in linked for s in f.get("sources") or [] if s.get("publisher")}
    out = {
        "state": "measured",
        "principal": principal,
        "issue": issue,
        "findings": linked[:most],
        "findings_total": len(linked),
        "principal_findings": len(findings or []),
        "counts": {"findings": len(linked), "established": len(established),
                   "corroborated": len(corroborated), "alleged": len(alleged),
                   "primary": len(primary), "publishers": len(publishers)},
        "patterns": patterns.detect(linked, principal) if len(linked) >= 1 else [],
        "timeline": [{"date": day, **entry} for day, entry in
                     sorted(from_facts.timeline_from_record(facts.by_date(linked)).items())],
    }
    out["lead"] = lead(out["counts"], principal, issue, len(findings or []))
    return out


def lead(counts: dict, principal: str, issue: str, principal_findings: int) -> str:
    """The section's first line, counted. Every number in it is in `counts`."""
    n = counts["findings"]
    if not n:
        if principal_findings:
            return (f"No verified statement in the sources read names both {principal} and {issue}. "
                    f"The record holds {principal_findings} finding(s) about {principal}, none about "
                    f"this issue: the connection is not established on the record.")
        return (f"No verified statement in the sources read names both {principal} and {issue}: "
                "the connection is not established on the record.")
    parts = [f"{n} verified statement{'s' if n != 1 else ''} on the record name{'' if n != 1 else 's'} "
             f"both {principal} and {issue}"]
    if counts["publishers"]:
        parts[0] += f", printed by {counts['publishers']} outlet{'s' if counts['publishers'] != 1 else ''}"
    detail = []
    if counts["corroborated"]:
        detail.append(f"{counts['corroborated']} independently corroborated")
    if counts["alleged"]:
        detail.append(f"{counts['alleged']} alleged, not established")
    if counts["primary"]:
        detail.append(f"{counts['primary']} resting on an official record")
    return parts[0] + (": " + "; ".join(detail) if detail else "") + "."


def not_measured(reason: str) -> dict:
    """The record could not be read for this map — never "no connection"."""
    return {"state": "not_measured", "reason": reason}

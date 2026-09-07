"""Catch the house-style violations a prompt instruction can't guarantee it
prevented — first person and hedging, specifically, because those are the
two things the client explicitly flagged: "the AI right now is saying 'I
believe this is what is happening', and we don't know. That's not how it
works in this industry."

This never rewrites prose automatically — silently editing a model's output
risks distorting a claim nobody then checks. It flags. The flag travels with
the report so a reader (or an editor) can see exactly where house style was
violated, the same transparency this codebase already gives derived sections
and unresolved citations.
"""

from __future__ import annotations

import re

# Every one of these SHOULD be structurally impossible after the house-style
# instruction in GROUNDING_RULES — this exists because "should be impossible"
# and "is impossible" are not the same claim, and only one of them is checked.
_FIRST_PERSON = re.compile(
    r"\b(i believe|i think|in my (view|opinion)|it seems to me|i would say|"
    r"i suspect|my (view|opinion|read) is)\b", re.IGNORECASE)

_HEDGES = re.compile(
    r"\b(perhaps|arguably|it could be argued|somewhat|it seems|seemingly|"
    r"may (suggest|indicate)|possibly|presumably)\b", re.IGNORECASE)


def check(text: str) -> list[dict]:
    """Every house-style violation found, with the offending phrase and where."""
    if not text:
        return []
    hits: list[dict] = []
    for pattern, kind in ((_FIRST_PERSON, "first_person"), (_HEDGES, "hedge")):
        for match in pattern.finditer(text):
            hits.append({"kind": kind, "phrase": match.group(0),
                        "context": text[max(0, match.start() - 30):match.end() + 30].strip()})
    return hits


def annotate(analysis: dict, fields: tuple[str, ...] = ("involvement", "tension_or_risk", "verdict")) -> dict:
    """Attach `style_flags` to an issue-map analysis for any prose field that
    violates house style, without touching the prose itself. Returns a new
    dict; the input is not mutated."""
    if not analysis:
        return analysis
    out = dict(analysis)
    flags: dict[str, list[dict]] = {}
    for field in fields:
        hits = check(out.get(field) or "")
        if hits:
            flags[field] = hits
    if flags:
        out["style_flags"] = flags
    return out

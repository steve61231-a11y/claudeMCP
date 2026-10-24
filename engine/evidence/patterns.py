"""What the record shows when it is read as a whole — counted, not imagined.

The insight a client pays an analyst for is rarely in one article. It is in
the shape of all of them: the story that seemed everywhere and traces to one
newsroom; the claim half the press states and half calls an allegation; the
outlet that set the agenda everyone else followed; how much of what is said
about a person is accusation rather than established fact. An analyst finds
these by hours of cross-reading. Each is also arithmetic over the verified
record (`facts.corroborate`), so here they are counted — every number in a
pattern's sentence is a count of findings or sources, and every pattern
names the findings it rests on. Nothing in this module can invent, because
nothing in it writes anything it did not count.

Patterns are shown on the page as they are, and handed to the brief's
writer as citable entries (`P1`…), so its sentences may draw on them and the
sentence check holds them to the same rules as any finding.
"""

from __future__ import annotations

from collections import Counter

_ESTABLISHED = {"reported", "inferred"}
_CONTESTED = {"alleged", "unresolved"}
_PRIMARY = {"gazette", "judgment"}
#: Below this many findings, shares and leaders are anecdote, not pattern.
MIN_FINDINGS = 3


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


def detect(findings: list[dict], subject: str = "") -> list[dict]:
    """Each pattern: kind, sentence (every number in it counted here),
    numbers, and the statements of the findings it rests on."""
    out: list[dict] = []
    if not findings:
        return out

    # One origin, many mastheads: the story that looks everywhere.
    for f in findings:
        pubs = f.get("publishers") or 0
        if f.get("syndicated") and pubs >= 3 and f.get("independent_sources") == 1:
            out.append({
                "kind": "echo",
                "sentence": (f"{_n(pubs, 'outlet')} carried \"{f['statement']}\", and all of it "
                             f"traces to a single original report."),
                "numbers": {"outlets": pubs, "origins": 1},
                "findings": [f["statement"]],
            })

    # Outlets that do not agree on whether it is established.
    for f in findings:
        statuses = {s.get("status") for s in f.get("sources") or []}
        if statuses & _ESTABLISHED and statuses & _CONTESTED:
            states = sum(1 for s in f["sources"] if s.get("status") in _ESTABLISHED)
            hedges = sum(1 for s in f["sources"] if s.get("status") in _CONTESTED)
            out.append({
                "kind": "contested",
                "sentence": (f"{_n(states, 'source')} report \"{f['statement']}\" as established "
                             f"and {_n(hedges, 'source')} only as an allegation; the record treats "
                             f"it as alleged."),
                "numbers": {"stating": states, "hedging": hedges},
                "findings": [f["statement"]],
            })

    if len(findings) < MIN_FINDINGS:
        return out

    # How much of the record is accusation.
    contested = [f for f in findings if f.get("status") in _CONTESTED]
    if contested:
        out.append({
            "kind": "balance",
            "sentence": (f"{len(contested)} of the {len(findings)} findings on the record about "
                         f"{subject or 'the subject'} are allegations or disputed, not established."),
            "numbers": {"contested": len(contested), "findings": len(findings)},
            "findings": [f["statement"] for f in contested[:5]],
        })

    # Who broke the stories that others then carried.
    first: Counter = Counter()
    carried = [f for f in findings if (f.get("publishers") or 0) >= 2]
    for f in carried:
        dated = [s for s in f.get("sources") or [] if s.get("published_at")]
        if dated:
            first[min(dated, key=lambda s: str(s["published_at"]))["publisher"]] += 1
    if first and len(carried) >= 2:
        leader, count = first.most_common(1)[0]
        if count >= 2:
            out.append({
                "kind": "agenda",
                "sentence": (f"{leader} was first with {count} of the {len(carried)} findings that "
                             f"more than one outlet carried."),
                "numbers": {"first": count, "carried": len(carried)},
                "findings": [f["statement"] for f in carried[:5]],
            })

    # What rests on the state's own records rather than on press accounts.
    primary = [f for f in findings
               if any(s.get("source_type") in _PRIMARY for s in f.get("sources") or [])]
    if primary:
        out.append({
            "kind": "primary",
            "sentence": (f"{len(primary)} of the {len(findings)} findings rest on an official "
                         f"record — a Gazette notice or a court judgment — not only on press "
                         f"reports."),
            "numbers": {"primary": len(primary), "findings": len(findings)},
            "findings": [f["statement"] for f in primary[:5]],
        })
    return out

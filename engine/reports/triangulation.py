"""Corroboration, made explicit rather than implicit.

"If we're talking about a fact... let's have triangulation, so we know the
AI is not hallucinating." A single quote from a single outlet is not a
confirmed fact — it's an unconfirmed report. This computes, from the
citations/quotes already attached to a claim, how many INDEPENDENT sources
(distinct outlets, not just distinct mentions — three reposts of one wire
story are one source, not three) actually back it, and marks the claim
accordingly. It never discards a single-source claim; it labels it honestly,
the same rule this codebase applies everywhere else (empty vs failed,
derived vs read, resolved vs unresolved).
"""

from __future__ import annotations

#: Below this many independent outlets, a claim is reported, not confirmed.
CONFIRMED_THRESHOLD = 2


def _platforms(rows: list[dict]) -> set[str]:
    """Distinct outlets among a set of citations/quotes. Only rows with a
    resolved URL count — an unresolved citation cannot be checked for
    independence, so it cannot be counted toward corroborating anything."""
    platforms = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        platform = row.get("platform")
        url = row.get("url")
        if platform and url:
            platforms.add(str(platform).lower())
    return platforms


def corroboration(rows: list[dict]) -> dict:
    """How well-attested is a claim backed by these citations/quotes?

    `rows` is any list of {"platform":..., "url":...} dicts — citations from
    citations.linkify, or quotes from an actor/narrative/timeline entry.
    """
    platforms = _platforms(rows)
    n = len(platforms)
    return {
        "independent_sources": n,
        "outlets": sorted(platforms),
        "confirmed": n >= CONFIRMED_THRESHOLD,
        "label": (f"Confirmed by {n} independent sources" if n >= CONFIRMED_THRESHOLD
                 else "Reported by a single source — unconfirmed" if n == 1
                 else "No independently-traceable source"),
    }


def annotate(analysis: dict) -> dict:
    """Attach corroboration to the verdict (the single most load-bearing
    claim in the map) and to every timeline event. Returns a new dict; the
    input is not mutated.
    """
    if not analysis:
        return analysis
    out = dict(analysis)

    verdict_rows = (out.get("verdict_citations") or []) + (out.get("involvement_citations") or [])
    if verdict_rows:
        out["verdict_corroboration"] = corroboration(verdict_rows)

    timeline = out.get("timeline")
    if timeline:
        new_timeline = []
        for event in timeline:
            if not isinstance(event, dict):
                new_timeline.append(event)
                continue
            event = dict(event)
            event["corroboration"] = corroboration(event.get("quotes") or [])
            new_timeline.append(event)
        out["timeline"] = new_timeline

    return out

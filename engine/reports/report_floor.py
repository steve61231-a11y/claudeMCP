"""What a report says when the analysts could not be reached.

The Search page streams a section at a time and ticks each one off as it
lands. When the provider refuses, every analyst returns its fallback — `[]` or
`{}` — so nothing lands, nothing ticks, and after forty-five minutes the reader
has a page with sentiment on it and nothing else. The mentions were collected,
stored and counted; they simply were never described.

Three of those sections do not need a model at all. Which platforms carried
the story, who the loudest accounts were, and when things happened are
questions about counting, and counting cannot refuse. The fourth — what people
are saying — can be approximated by the terms the coverage turns on, which is
weaker than a reading and better than a blank.

Everything produced here is marked `derived: true`, and the page says which
sections were counted rather than read. An unmarked derived section is a lie
about how much work was done.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from engine.reports import issue_floor


def _engagement(mention: dict) -> int:
    counts = mention.get("engagement") or {}
    return sum(int(counts.get(k) or 0) for k in ("views", "likes", "shares", "comments"))


def _ref(mention: dict) -> dict:
    return {"ref": str(mention.get("id", ""))[:8],
            "text": (mention.get("text") or mention.get("title") or "").strip()[:240]}


def platform_pulse(mentions: list[dict]) -> list[dict]:
    """Which platforms carried this, and how loudly. Pure counting."""
    by_platform: dict[str, list[dict]] = {}
    for mention in mentions:
        by_platform.setdefault(mention.get("platform") or "unknown", []).append(mention)
    if not by_platform:
        return []

    out = []
    for platform, items in sorted(by_platform.items(), key=lambda kv: -len(kv[1]))[:8]:
        loudest = sorted(items, key=_engagement, reverse=True)
        handles = [h for h in ({str(m.get("author_handle") or "") for m in loudest[:12]})
                   if h and h != "None"][:5]
        total = sum(_engagement(m) for m in items)
        out.append({
            "platform": platform,
            "tone": (f"{len(items)} of the {len(mentions)} items collected came from "
                     f"{platform}, carrying {total:,} recorded interactions. This is a "
                     "count of what was collected, not a reading of what was said — no "
                     "analyst described this platform's tone."),
            "themes": [],
            "notable_voices": handles,
            "quotes": [_ref(m) for m in loudest[:3] if (m.get("text") or m.get("title"))],
            "derived": True,
        })
    return out


def influencer_stances(mentions: list[dict], influence_summary: list[dict]) -> list[dict]:
    """The accounts that carried the most weight. Counting again."""
    ranked = [row for row in (influence_summary or []) if row.get("author_handle")][:10]
    if not ranked:
        counts: Counter = Counter()
        for mention in mentions:
            handle = mention.get("author_handle")
            if handle:
                counts[handle] += max(1, _engagement(mention))
        ranked = [{"author_handle": h, "score": c} for h, c in counts.most_common(10)]

    by_handle: dict[str, list[dict]] = {}
    for mention in mentions:
        by_handle.setdefault(mention.get("author_handle") or "", []).append(mention)

    out = []
    for row in ranked:
        handle = row["author_handle"]
        theirs = sorted(by_handle.get(handle, []), key=_engagement, reverse=True)
        if not theirs:
            continue
        platforms = sorted({str(m.get("platform") or "?") for m in theirs})
        out.append({
            "handle": handle,
            "account_type": "unclassified",
            "stance": "not established",
            "what_they_say": (f"{len(theirs)} item(s) on {', '.join(platforms)}, "
                              f"{sum(_engagement(m) for m in theirs):,} recorded "
                              "interactions. The stance is not established — this account "
                              "was ranked by reach, not read by an analyst."),
            "quotes": [_ref(m) for m in theirs[:2] if (m.get("text") or m.get("title"))],
            "derived": True,
        })
    return out


def timeline(mentions: list[dict]) -> list[dict]:
    """Dated developments, straight from the record."""
    return [
        {"date": item["date"], "title": item["event"], "event": item["event"],
         "what_happened": ("Taken verbatim from a document published that day. No analyst "
                           "described it."),
         "sources": item.get("sources", 1), "quotes": item.get("quotes") or [],
         "derived": True}
        for item in issue_floor.timeline(mentions, limit=30)
    ]


def public_voice(mentions: list[dict], name: str) -> dict:
    """The terms the coverage turns on. Weak, honest, not blank."""
    themes = issue_floor.themes(mentions, name, "", limit=10)
    if not themes:
        return {}
    return {
        "themes": [
            {"theme": theme["narrative"],
             "summary": (f"{theme['framing']} This is a count of where the coverage "
                         "concentrates, not a description of what was said about it."),
             "quotes": [{"ref": "", "text": q.get("text", "")}
                        for q in (theme.get("quotes") or [])],
             "derived": True}
            for theme in themes
        ],
        "derived": True,
    }


def executive_summary(mentions: list[dict], name: str, coverage: dict | None = None) -> str:
    """What was collected, stated plainly. Never a claim about the subject."""
    if not mentions:
        return ""
    platforms = sorted({str(m.get("platform") or "?") for m in mentions})
    dates = sorted(d for d in (str(m.get("posted_at") or "")[:10] for m in mentions) if d)
    span = f" spanning {dates[0]} to {dates[-1]}" if dates else ""
    note = ""
    if coverage and coverage.get("mentions_analyzed") == 0:
        note = (" No analyst section could be produced: the model backend did not answer. "
                "What follows was counted from the documents, not read.")
    return (f"{len(mentions)} items about {name} were collected from "
            f"{len(platforms)} sources ({', '.join(platforms[:6])}){span}."
            f"{note}")


#: Section -> is it empty? Some are lists, some dicts, one is a string.
def _is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return not any(value.values())
    return not value


def fill(payload: dict, mentions: list[dict], name: str) -> list[str]:
    """Fill the sections no analyst wrote. Returns the names of those filled."""
    if not mentions:
        return []

    derived: list[str] = []
    candidates = {
        "platform_pulse": lambda: platform_pulse(mentions),
        "influencer_stances": lambda: influencer_stances(
            mentions, payload.get("influence_summary") or []),
        "timeline": lambda: timeline(mentions),
        "public_voice": lambda: public_voice(mentions, name),
    }
    for key, build in candidates.items():
        if not _is_empty(payload.get(key)):
            continue
        try:
            value = build()
        except Exception:  # noqa: BLE001 — a floor that raises is worse than no floor
            continue
        if not _is_empty(value):
            payload[key] = value
            derived.append(key)

    if _is_empty(payload.get("executive_summary")):
        summary = executive_summary(mentions, name, payload.get("coverage"))
        if summary:
            payload["executive_summary"] = summary
            derived.append("executive_summary")

    if derived:
        payload["derived_sections"] = sorted(set(
            (payload.get("derived_sections") or []) + derived))
    return derived

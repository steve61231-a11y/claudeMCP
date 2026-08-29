"""Period-over-period series — the client's four dashboard infographics.

The reference dashboard is built almost entirely on movement between reporting
periods, not on a single snapshot:

  - "Tonalité des articles sur <subject>"  — 100% stacked columns, tone split
                                             per period,
  - "Personnalités (nb d'occurrences)"     — 100% stacked area, who owns the
                                             conversation, period by period,
  - "Climat politique (nb d'occurrences)"  — 100% stacked area, which themes
                                             dominate, period by period.

None of that existed. The dashboard's tone chart read `payload["timeline"]`,
which is the ANALYST's dated event list — `{date, event, quotes}` — and helped
itself to `.positive` / `.neutral` / `.negative`, which are not fields on it. It
plotted zeros on every report ever produced.

Everything here is arithmetic over data already held: mention timestamps, the
per-mention sentiment records, and the narrative clusters' own membership. No
model call, so a period series is never the reason a report is slow or thin.
"""

from collections import Counter
from datetime import datetime, timedelta

DEFAULT_BUCKETS = 3
MAX_SERIES_KEYS = 7  # legend legibility; the reference charts show ~7 bands


def _bucket_bounds(window_start: datetime, window_end: datetime, buckets: int):
    """Equal-length periods covering the window, oldest first."""
    span = (window_end - window_start) / max(buckets, 1)
    edges = [window_start + span * i for i in range(buckets + 1)]
    edges[-1] = window_end
    return list(zip(edges[:-1], edges[1:]))


def _label(start: datetime, end: datetime) -> str:
    """Short, unambiguous period label, e.g. "9-14 Feb"."""
    if start.month == end.month:
        return f"{start.day}-{end.day} {start:%b}"
    return f"{start:%d %b}-{end:%d %b}"


def _index_by_period(mentions: list[dict], bounds) -> list[list[dict]]:
    """Mentions falling in each period. An undated mention belongs to none —
    guessing a bucket for it would invent movement that never happened."""
    buckets: list[list[dict]] = [[] for _ in bounds]
    for mention in mentions:
        posted = mention.get("posted_at")
        if not isinstance(posted, datetime):
            continue
        for i, (start, end) in enumerate(bounds):
            # Last bucket is closed so the final instant is not dropped.
            if start <= posted < end or (i == len(bounds) - 1 and posted == end):
                buckets[i].append(mention)
                break
    return buckets


def _top_keys(counters: list[Counter], limit: int) -> list[str]:
    """The keys worth charting, ranked across the whole window rather than
    within one period — otherwise the bands change meaning between columns."""
    total: Counter = Counter()
    for counter in counters:
        total.update(counter)
    return [key for key, _ in total.most_common(limit)]


def build_period_series(
    mentions: list[dict],
    sentiments: dict[str, dict],
    narratives: list[dict] | None,
    people: list[dict] | None,
    window_start: datetime,
    window_end: datetime,
    buckets: int = DEFAULT_BUCKETS,
) -> dict:
    """The three period-over-period series the dashboard charts.

    `sentiments` is {mention_id: {"sentiment": ...}}; `narratives` carry the
    mention_ids that belong to them; `people` are the co-mentioned individuals
    already extracted for the network view.
    """
    bounds = _bucket_bounds(window_start, window_end, buckets)
    labels = [_label(start, end) for start, end in bounds]
    per_period = _index_by_period(mentions, bounds)

    # 1. Tone about the subject, period by period.
    tone = []
    for label, group in zip(labels, per_period):
        counts = Counter(
            (sentiments.get(str(m.get("id"))) or {}).get("sentiment")
            for m in group
        )
        tone.append({
            "label": label,
            "values": {
                "positive": counts.get("positive", 0),
                "neutral": counts.get("neutral", 0),
                "negative": counts.get("negative", 0),
            },
            "scored": sum(v for k, v in counts.items() if k),
            "mentions": len(group),
        })

    # 2. Which people own the conversation, period by period. Counted by name
    #    occurrence in the text, which is what "nb d'occurrences" means and is
    #    checkable against the corpus.
    names = [p.get("name") for p in (people or []) if p.get("name")][:20]
    people_counters = []
    for group in per_period:
        counter: Counter = Counter()
        for mention in group:
            text = (mention.get("text") or "").lower()
            for name in names:
                if name.lower() in text:
                    counter[name] += 1
        people_counters.append(counter)
    people_keys = _top_keys(people_counters, MAX_SERIES_KEYS)

    # 3. Which themes dominate, period by period. Exact: a narrative already
    #    knows which mentions belong to it.
    narrative_members = {
        n.get("label"): set(n.get("mention_ids") or [])
        for n in (narratives or [])
        if n.get("label")
    }
    theme_counters = []
    for group in per_period:
        ids = {str(m.get("id")) for m in group}
        theme_counters.append(Counter({
            label: len(ids & {str(i) for i in members})
            for label, members in narrative_members.items()
        }))
    theme_keys = _top_keys(theme_counters, MAX_SERIES_KEYS)

    def _series(keys, counters):
        return {
            "keys": keys,
            "periods": [
                {"label": label, "values": {k: counter.get(k, 0) for k in keys}}
                for label, counter in zip(labels, counters)
            ],
        }

    return {
        "labels": labels,
        "buckets": buckets,
        "tone": tone,
        "personalities": _series(people_keys, people_counters),
        "themes": _series(theme_keys, theme_counters),
    }

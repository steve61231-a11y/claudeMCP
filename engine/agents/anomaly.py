"""Anomaly detection — finding what deserves attention before it is obvious.

Volume-ranked reporting tells you what is already loud. By the time a story is
loud, everyone has it. The value an analyst adds is noticing the small thing
early: a name that has never appeared before, a story only one outlet is
carrying, a relationship that shouldn't exist, twenty accounts posting the same
sentence within an hour.

This agent looks specifically for those. Every signal it raises is grounded —
it names the entities, events or accounts involved, so a human can check it
rather than take the label on trust. Signals are observations, not conclusions:
"only one source carries this" is a fact about the evidence, and whether that
means *scoop* or *fabrication* is a judgement the analyst makes.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func

from engine.db.models import (
    Document,
    Entity,
    EntityRelationship,
    Event,
    RawMention,
)

# Severity is about how much attention a signal deserves, not how bad the news is.
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

_RECENT_HOURS = 72
_COORDINATION_WINDOW_HOURS = 24
_COORDINATION_MIN_ACCOUNTS = 4


def _norm(text: str) -> str:
    """Normalise for near-duplicate comparison."""
    cleaned = re.sub(r"http\S+", "", (text or "").lower())
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.split())


def detect_first_appearances(db, politician, hours: int = _RECENT_HOURS) -> list[dict]:
    """Entities appearing in this subject's world for the first time.

    A new name is the earliest possible signal — it precedes the story that
    will eventually be written about it.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(Entity)
        .filter(Entity.first_seen >= since)
        .order_by(Entity.mention_count.desc().nullslast())
        .limit(15)
        .all()
    )
    signals = []
    for entity in rows:
        signals.append(
            {
                "kind": "first_appearance",
                "severity": INFO if (entity.mention_count or 0) < 3 else WARNING,
                "headline": f"New {entity.type} in the picture: {entity.name}",
                "detail": (
                    f"'{entity.name}' had not appeared in this subject's corpus before "
                    f"{entity.first_seen:%Y-%m-%d}; seen {entity.mention_count or 0} time(s) since."
                ),
                "entities": [entity.name],
            }
        )
    return signals


def detect_single_source_claims(db, politician) -> list[dict]:
    """Events that only one outlet is carrying.

    Deliberately neutral: an uncorroborated story may be an exclusive or may be
    wrong, and the honest output is to flag that it rests on one source rather
    than to decide which.
    """
    events = (
        db.query(Event)
        .filter(Event.politician_id == politician.id, Event.independent_domains <= 1)
        .order_by(Event.first_seen.desc().nullslast())
        .limit(10)
        .all()
    )
    return [
        {
            "kind": "single_source",
            "severity": WARNING,
            "headline": f"Only one source reports: {event.title}",
            "detail": (
                "No independent corroboration found in the corpus. This may be an "
                "exclusive or may not hold up — it should be checked before it is relied on."
            ),
            "events": [event.title],
        }
        for event in events
    ]


def detect_coordinated_messaging(db, politician) -> list[dict]:
    """Many distinct accounts posting near-identical text in a short window.

    Organic reaction varies in wording; copy-paste repetition across accounts is
    a different phenomenon, and it inflates every volume-based metric unless it
    is named.
    """
    since = datetime.utcnow() - timedelta(hours=_COORDINATION_WINDOW_HOURS)
    mentions = (
        db.query(RawMention)
        .filter(
            RawMention.politician_id == politician.id,
            RawMention.posted_at >= since,
            RawMention.source_type != "article",
        )
        .limit(2000)
        .all()
    )

    buckets: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, str] = {}
    for mention in mentions:
        normalized = _norm(mention.text)
        if len(normalized.split()) < 5:
            continue  # too short to be distinctive
        key = " ".join(normalized.split()[:12])
        buckets[key].add(mention.author_handle or "?")
        examples.setdefault(key, (mention.text or "")[:200])

    signals = []
    for key, handles in buckets.items():
        if len(handles) >= _COORDINATION_MIN_ACCOUNTS:
            signals.append(
                {
                    "kind": "coordinated_messaging",
                    "severity": WARNING,
                    "headline": f"{len(handles)} accounts posted near-identical text",
                    "detail": (
                        f"Within {_COORDINATION_WINDOW_HOURS}h, {len(handles)} distinct accounts "
                        f"posted nearly the same wording: \"{examples[key]}\". Volume metrics "
                        "should be read with this in mind."
                    ),
                    "accounts": sorted(handles)[:10],
                }
            )
    return signals[:5]


def detect_emerging_stories(db, politician, hours: int = _RECENT_HOURS) -> list[dict]:
    """Events that are new and gaining sources — small today, possibly not tomorrow."""
    since = datetime.utcnow() - timedelta(hours=hours)
    events = (
        db.query(Event)
        .filter(
            Event.politician_id == politician.id,
            Event.first_seen >= since,
            Event.independent_domains >= 2,
        )
        .order_by(Event.independent_domains.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "kind": "emerging_story",
            "severity": WARNING if event.independent_domains >= 3 else INFO,
            "headline": f"Emerging: {event.title}",
            "detail": (
                f"First seen {event.first_seen:%Y-%m-%d}, already carried by "
                f"{event.independent_domains} independent sources."
            ),
            "events": [event.title],
        }
        for event in events
    ]


def detect_unexpected_relationships(db, politician, hours: int = _RECENT_HOURS) -> list[dict]:
    """Newly observed connections between entities.

    A link appearing for the first time between two previously unconnected
    parties is exactly the kind of thing that never makes a headline but often
    explains one.
    """
    since = datetime.utcnow() - timedelta(hours=hours)
    edges = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.politician_id == politician.id,
            EntityRelationship.first_seen >= since,
            EntityRelationship.rel_type != "mentioned_with",
        )
        .order_by(EntityRelationship.confidence.desc().nullslast())
        .limit(10)
        .all()
    )
    signals = []
    for edge in edges:
        source = db.get(Entity, edge.source_entity_id)
        target = db.get(Entity, edge.target_entity_id)
        if not source or not target:
            continue
        signals.append(
            {
                "kind": "new_relationship",
                "severity": INFO,
                "headline": f"New connection: {source.name} — {edge.rel_type} — {target.name}",
                "detail": f"First observed {edge.first_seen:%Y-%m-%d}, across {edge.evidence_count or 0} source(s).",
                "entities": [source.name, target.name],
            }
        )
    return signals


def detect_quiet_periods(db, politician) -> list[dict]:
    """Unusual silence.

    Absence is a signal too: a subject who is normally covered daily going quiet
    for a week is itself information, and volume-ranked reporting never shows it
    because there is nothing to rank.
    """
    now = datetime.utcnow()
    recent = (
        db.query(func.count(RawMention.id))
        .filter(RawMention.politician_id == politician.id,
                RawMention.posted_at >= now - timedelta(days=7))
        .scalar()
        or 0
    )
    baseline = (
        db.query(func.count(RawMention.id))
        .filter(
            RawMention.politician_id == politician.id,
            RawMention.posted_at >= now - timedelta(days=37),
            RawMention.posted_at < now - timedelta(days=7),
        )
        .scalar()
        or 0
    )
    weekly_baseline = baseline / 30 * 7 if baseline else 0
    if weekly_baseline >= 5 and recent < weekly_baseline * 0.4:
        return [
            {
                "kind": "quiet_period",
                "severity": INFO,
                "headline": "Coverage has dropped sharply",
                "detail": (
                    f"{recent} mentions in the last 7 days against a typical {weekly_baseline:.0f}. "
                    "A fall in attention can precede or follow a change worth understanding."
                ),
            }
        ]
    return []


def detect_all(db, politician) -> dict:
    """Run every detector. Each is independent, so one failing can't blind the rest."""
    detectors = (
        ("first_appearances", detect_first_appearances),
        ("single_source", detect_single_source_claims),
        ("coordinated_messaging", detect_coordinated_messaging),
        ("emerging_stories", detect_emerging_stories),
        ("new_relationships", detect_unexpected_relationships),
        ("quiet_periods", detect_quiet_periods),
    )

    signals: list[dict] = []
    failed: list[str] = []
    for name, detector in detectors:
        try:
            signals.extend(detector(db, politician))
        except Exception:  # noqa: BLE001 — never let one detector suppress the others
            failed.append(name)

    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    signals.sort(key=lambda s: order.get(s.get("severity"), 3))
    result = {"count": len(signals), "signals": signals[:30]}
    if failed:
        result["detectors_failed"] = failed
    return result

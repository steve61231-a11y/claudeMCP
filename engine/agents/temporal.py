"""Temporal intelligence — reporting what CHANGED, not just what is.

A report that re-describes the present every time is a status page. An analyst
asks a different question: what is different since last time? A name that has
never appeared before, a narrative that doubled in a week, sentiment that turned
— those are the things worth someone's attention, and none of them are visible
in a single snapshot no matter how detailed it is.

So each run stores a compact fingerprint of the subject's state, and the next
run diffs against it. Movement becomes a first-class output rather than
something a reader has to reconstruct by comparing two documents.

Snapshots are deliberately small: counts and per-item strengths, not copies of
the corpus. The evidence already lives in the corpus; this is only the shape of
it over time.
"""

from datetime import datetime, timedelta

from sqlalchemy import func

from engine.db.models import (
    Document,
    Entity,
    EntityRelationship,
    Event,
    MentionSentiment,
    Narrative,
    NarrativeMetric,
    RawMention,
    Snapshot,
)


def take_snapshot(db, politician, run_id: str | None = None) -> Snapshot:
    """Record the current shape of what we know about this subject."""
    now = datetime.utcnow()

    mentions_total = (
        db.query(func.count(RawMention.id)).filter(RawMention.politician_id == politician.id).scalar() or 0
    )
    documents_total = (
        db.query(func.count(Document.id)).filter(Document.politician_id == politician.id).scalar() or 0
    )
    events_total = (
        db.query(func.count(Event.id)).filter(Event.politician_id == politician.id).scalar() or 0
    )
    relationships_total = (
        db.query(func.count(EntityRelationship.id))
        .filter(EntityRelationship.politician_id == politician.id)
        .scalar()
        or 0
    )

    sentiment_counts = dict(
        db.query(MentionSentiment.sentiment, func.count(MentionSentiment.mention_id))
        .join(RawMention, RawMention.id == MentionSentiment.mention_id)
        .filter(RawMention.politician_id == politician.id)
        .group_by(MentionSentiment.sentiment)
        .all()
    )

    # Per-entity and per-narrative strengths: enough to see movement, small
    # enough to store every run.
    entity_state = {
        entity.canonical_key: entity.mention_count or 0
        for entity in db.query(Entity)
        .filter(Entity.canonical_key.isnot(None))
        .order_by(Entity.mention_count.desc().nullslast())
        .limit(200)
        .all()
    }

    narrative_state: dict[str, float] = {}
    for narrative in db.query(Narrative).filter_by(politician_id=politician.id).all():
        metric = (
            db.query(NarrativeMetric)
            .filter_by(narrative_id=narrative.id)
            .order_by(NarrativeMetric.computed_at.desc().nullslast())
            .first()
        )
        narrative_state[narrative.label] = float(getattr(metric, "strength_score", 0) or 0)

    snapshot = Snapshot(
        politician_id=politician.id,
        run_id=run_id,
        taken_at=now,
        metrics={
            "mentions_total": int(mentions_total),
            "documents_total": int(documents_total),
            "events_total": int(events_total),
            "relationships_total": int(relationships_total),
            "sentiment": {k: int(v) for k, v in sentiment_counts.items()},
        },
        entity_state=entity_state,
        narrative_state=narrative_state,
    )
    db.add(snapshot)
    db.flush()

    previous = (
        db.query(Snapshot)
        .filter(Snapshot.politician_id == politician.id, Snapshot.id != snapshot.id)
        .order_by(Snapshot.taken_at.desc())
        .first()
    )
    snapshot.delta = diff_snapshots(previous, snapshot)
    db.commit()
    return snapshot


def diff_snapshots(previous: Snapshot | None, current: Snapshot) -> dict:
    """What moved between two points in time.

    With no previous snapshot this is a baseline, not a change — saying so
    explicitly stops a first run from being read as a surge.
    """
    if previous is None:
        return {"baseline": True, "note": "first snapshot — nothing to compare yet"}

    prev_metrics = previous.metrics or {}
    curr_metrics = current.metrics or {}

    def _delta(key: str) -> dict:
        before = int(prev_metrics.get(key, 0) or 0)
        after = int(curr_metrics.get(key, 0) or 0)
        return {"before": before, "after": after, "change": after - before}

    prev_entities = previous.entity_state or {}
    curr_entities = current.entity_state or {}
    new_entities = [k for k in curr_entities if k not in prev_entities]
    grown_entities = sorted(
        (
            {"entity": k, "before": prev_entities[k], "after": v, "change": v - prev_entities[k]}
            for k, v in curr_entities.items()
            if k in prev_entities and v > prev_entities[k]
        ),
        key=lambda d: d["change"],
        reverse=True,
    )[:10]

    prev_narratives = previous.narrative_state or {}
    curr_narratives = current.narrative_state or {}
    new_narratives = [k for k in curr_narratives if k not in prev_narratives]
    narrative_moves = sorted(
        (
            {
                "narrative": k,
                "before": round(prev_narratives[k], 2),
                "after": round(v, 2),
                "change": round(v - prev_narratives[k], 2),
            }
            for k, v in curr_narratives.items()
            if k in prev_narratives and abs(v - prev_narratives[k]) > 0.01
        ),
        key=lambda d: abs(d["change"]),
        reverse=True,
    )[:10]

    return {
        "baseline": False,
        "since": str(previous.taken_at),
        "mentions": _delta("mentions_total"),
        "documents": _delta("documents_total"),
        "events": _delta("events_total"),
        "relationships": _delta("relationships_total"),
        "sentiment_shift": _sentiment_shift(prev_metrics.get("sentiment") or {},
                                            curr_metrics.get("sentiment") or {}),
        "new_entities": new_entities[:20],
        "entities_gaining_attention": grown_entities,
        "new_narratives": new_narratives[:10],
        "narrative_movement": narrative_moves,
    }


def _sentiment_shift(before: dict, after: dict) -> dict:
    """Change in the negative share — the number people actually react to.

    Expressed as shares rather than counts: a rise from 10 to 20 negative
    mentions means something quite different if total volume tripled.
    """
    def negative_share(counts: dict) -> float | None:
        total = sum(int(v or 0) for v in counts.values())
        if not total:
            return None
        return round(100 * int(counts.get("negative", 0) or 0) / total, 1)

    before_share = negative_share(before)
    after_share = negative_share(after)
    if before_share is None or after_share is None:
        return {"before": before_share, "after": after_share, "change": None}
    return {
        "before": before_share,
        "after": after_share,
        "change": round(after_share - before_share, 1),
    }


def window_activity(db, politician, hours: int) -> dict:
    """Raw activity inside a recent window, for 'what changed today/this week'."""
    since = datetime.utcnow() - timedelta(hours=hours)
    mentions = (
        db.query(func.count(RawMention.id))
        .filter(RawMention.politician_id == politician.id, RawMention.posted_at >= since)
        .scalar()
        or 0
    )
    documents = (
        db.query(func.count(Document.id))
        .filter(Document.politician_id == politician.id, Document.fetched_at >= since)
        .scalar()
        or 0
    )
    events = (
        db.query(func.count(Event.id))
        .filter(Event.politician_id == politician.id, Event.first_seen >= since)
        .scalar()
        or 0
    )
    new_entities = (
        db.query(func.count(Entity.id)).filter(Entity.first_seen >= since).scalar() or 0
    )
    return {
        "mentions": int(mentions),
        "documents": int(documents),
        "new_events": int(events),
        "new_entities": int(new_entities),
    }


def temporal_summary(db, politician, run_id: str | None = None) -> dict:
    """Snapshot now, and report movement across the horizons an analyst uses."""
    snapshot = take_snapshot(db, politician, run_id=run_id)
    return {
        "since_last_run": snapshot.delta,
        "last_24h": window_activity(db, politician, 24),
        "last_7d": window_activity(db, politician, 24 * 7),
        "last_30d": window_activity(db, politician, 24 * 30),
        "snapshot_at": str(snapshot.taken_at),
    }

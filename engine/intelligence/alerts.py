"""Early-warning detection: turns the accumulated corpus into alerts.

Runs after every ingestion sweep. Compares the most recent activity window
against the preceding baseline window and raises Alert rows for changes the
politician's team should hear about proactively:

- negative_spike: negative share in the recent window jumps vs baseline
- viral_post: a new post crosses an engagement threshold with negative tone
- narrative_surge: a narrative's recent mention velocity far exceeds its baseline
- new_amplifier: an author absent from the baseline suddenly posting repeatedly

Detection is pure SQL/aggregation — no LLM cost — so it can run on every sweep.
"""

import json
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from engine.config import settings
from engine.db.models import Alert, MentionSentiment, Politician, RawMention

RECENT_HOURS = 24
BASELINE_DAYS = 7
NEGATIVE_SPIKE_POINTS = 15.0  # pct-point jump that triggers a warning
VIRAL_ENGAGEMENT = 50000
SURGE_MIN_MENTIONS = 5
NEW_AMPLIFIER_MIN_POSTS = 3


def _engagement(m: RawMention) -> int:
    e = m.engagement_json or {}
    return sum(int(e.get(k) or 0) for k in ("views", "likes", "shares", "comments"))


def _evidence(mentions: list[RawMention], limit: int = 3) -> list[dict]:
    return [
        {
            "mention_id": m.id,
            "url": m.source_url,
            "text": (m.text or "")[:200],
            "author": m.author_handle,
            "platform": m.platform,
        }
        for m in mentions[:limit]
    ]


def detect_alerts(db: Session, politician: Politician, now: datetime | None = None) -> list[Alert]:
    """Runs all detectors; persists and returns new alerts (deduped against
    alerts already raised in the recent window)."""
    now = now or datetime.utcnow()
    recent_start = now - timedelta(hours=RECENT_HOURS)
    baseline_start = now - timedelta(days=BASELINE_DAYS)

    rows = (
        db.query(RawMention, MentionSentiment)
        .outerjoin(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
        .filter(
            RawMention.politician_id == politician.id,
            RawMention.posted_at >= baseline_start,
            RawMention.is_spam == 0,
        )
        .all()
    )
    recent = [(m, s) for m, s in rows if m.posted_at >= recent_start]
    baseline = [(m, s) for m, s in rows if m.posted_at < recent_start]

    candidates: list[Alert] = []
    candidates += _detect_negative_spike(politician, recent, baseline)
    candidates += _detect_viral_posts(politician, recent)
    candidates += _detect_new_amplifiers(politician, recent, baseline)

    # Dedupe: same kind+headline already raised in the last 24h → skip.
    existing = {
        (a.kind, a.headline)
        for a in db.query(Alert)
        .filter(Alert.politician_id == politician.id, Alert.created_at >= recent_start)
        .all()
    }
    new_alerts = [a for a in candidates if (a.kind, a.headline) not in existing]
    for alert in new_alerts:
        db.add(alert)
    db.commit()

    for alert in new_alerts:
        _deliver_webhook(db, alert, politician)
    return new_alerts


def _sent_share(pairs: list, label: str) -> float | None:
    scored = [s.sentiment for _, s in pairs if s is not None]
    if len(scored) < 5:  # too little signal to compare
        return None
    return 100.0 * sum(1 for x in scored if x == label) / len(scored)


def _detect_negative_spike(politician: Politician, recent: list, baseline: list) -> list[Alert]:
    recent_neg = _sent_share(recent, "negative")
    baseline_neg = _sent_share(baseline, "negative")
    if recent_neg is None or baseline_neg is None:
        return []
    jump = recent_neg - baseline_neg
    if jump < NEGATIVE_SPIKE_POINTS:
        return []
    worst = sorted(
        (m for m, s in recent if s is not None and s.sentiment == "negative"),
        key=_engagement,
        reverse=True,
    )
    return [
        Alert(
            politician_id=politician.id,
            severity="critical" if jump >= 2 * NEGATIVE_SPIKE_POINTS else "warning",
            kind="negative_spike",
            headline=f"Negative sentiment jumped {jump:.0f} points in the last {RECENT_HOURS}h",
            detail=f"Negative share is {recent_neg:.0f}% in the last {RECENT_HOURS}h vs {baseline_neg:.0f}% over the prior week.",
            evidence=_evidence(worst),
        )
    ]


def _detect_viral_posts(politician: Politician, recent: list) -> list[Alert]:
    alerts = []
    for m, s in recent:
        eng = _engagement(m)
        if eng < VIRAL_ENGAGEMENT:
            continue
        tone = s.sentiment if s is not None else "unscored"
        severity = "critical" if tone == "negative" else "info"
        alerts.append(
            Alert(
                politician_id=politician.id,
                severity=severity,
                kind="viral_post",
                headline=f"{tone.capitalize()} post by @{m.author_handle} reaching {eng:,} engagement on {m.platform}",
                detail=(m.text or "")[:280],
                evidence=_evidence([m]),
            )
        )
    return alerts


def _detect_new_amplifiers(politician: Politician, recent: list, baseline: list) -> list[Alert]:
    baseline_authors = {m.author_handle for m, _ in baseline}
    counts: dict[str, list[RawMention]] = {}
    for m, _ in recent:
        if m.author_handle and m.author_handle not in baseline_authors:
            counts.setdefault(m.author_handle, []).append(m)
    alerts = []
    for handle, posts in counts.items():
        if len(posts) < NEW_AMPLIFIER_MIN_POSTS:
            continue
        alerts.append(
            Alert(
                politician_id=politician.id,
                severity="warning",
                kind="new_amplifier",
                headline=f"New voice @{handle} posted {len(posts)}x about {politician.name} in {RECENT_HOURS}h",
                detail="An account with no prior activity in the baseline week is suddenly posting repeatedly.",
                evidence=_evidence(sorted(posts, key=_engagement, reverse=True)),
            )
        )
    return alerts


def _deliver_webhook(db: Session, alert: Alert, politician: Politician) -> None:
    """Best-effort push to ALERT_WEBHOOK_URL (Slack/Make/any JSON receiver).
    Failure never blocks detection; undelivered alerts stay flagged."""
    url = settings.alert_webhook_url
    if not url:
        return
    body = json.dumps(
        {
            "politician": politician.name,
            "severity": alert.severity,
            "kind": alert.kind,
            "headline": alert.headline,
            "detail": alert.detail,
            "evidence": alert.evidence,
            "created_at": str(alert.created_at),
        }
    ).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        alert.delivered = 1
        db.commit()
    except Exception:
        pass

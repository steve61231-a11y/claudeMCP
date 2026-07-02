"""Report-over-report change tracking.

The most persuasive number in a recurring intelligence product is the delta:
"positive sentiment moved +4.2 points since your last report." This module
compares the freshly generated payload against the politician's previous
stored IntelligenceReport and produces a compact `since_last_report` section,
plus a sentiment-over-time series from the full report history.
"""

from sqlalchemy.orm import Session

from engine.db.models import IntelligenceReport, Politician


def compute_deltas(db: Session, politician: Politician, current_payload: dict) -> dict | None:
    previous = (
        db.query(IntelligenceReport)
        .filter(IntelligenceReport.politician_id == politician.id)
        .order_by(IntelligenceReport.generated_at.desc())
        .first()  # the newest STORED report — current one isn't persisted yet
    )
    if previous is None:
        return None
    prev = previous.payload or {}

    cur_sent = current_payload.get("sentiment_breakdown") or {}
    prev_sent = prev.get("sentiment_breakdown") or {}
    sentiment_shift = {
        key: round(cur_sent.get(f"{key}_pct", 0) - prev_sent.get(f"{key}_pct", 0), 1)
        for key in ("positive", "neutral", "negative")
    }

    cur_vol = (current_payload.get("volume_trends") or {}).get("total_mentions", 0)
    prev_vol = (prev.get("volume_trends") or {}).get("total_mentions", 0)

    cur_rank = [n["label"] for n in current_payload.get("narrative_breakdown") or []]
    prev_rank = [n["label"] for n in prev.get("narrative_breakdown") or []]
    new_narratives = [label for label in cur_rank[:8] if label not in prev_rank]
    rank_moves = []
    for pos, label in enumerate(cur_rank[:8]):
        if label in prev_rank:
            move = prev_rank.index(label) - pos
            if move != 0:
                rank_moves.append({"label": label, "move": move})

    cur_inf = [i.get("author_handle") for i in (current_payload.get("influence_summary") or [])[:10]]
    prev_inf = [i.get("author_handle") for i in (prev.get("influence_summary") or [])[:10]]
    new_influencers = [h for h in cur_inf if h and h not in prev_inf]

    return {
        "previous_report_at": str(previous.generated_at),
        "sentiment_shift": sentiment_shift,
        "mention_volume_change": cur_vol - prev_vol,
        "new_narratives": new_narratives,
        "narrative_rank_moves": rank_moves,
        "new_influence_entrants": new_influencers,
    }


def sentiment_history(db: Session, politician: Politician, limit: int = 26) -> list[dict]:
    """Chronological sentiment series across stored reports — the trend line."""
    rows = (
        db.query(IntelligenceReport)
        .filter(IntelligenceReport.politician_id == politician.id)
        .order_by(IntelligenceReport.generated_at.desc())
        .limit(limit)
        .all()
    )
    series = []
    for r in reversed(rows):
        sent = (r.payload or {}).get("sentiment_breakdown") or {}
        if not sent:
            continue
        series.append(
            {
                "date": str(r.generated_at.date()),
                "positive": sent.get("positive_pct"),
                "neutral": sent.get("neutral_pct"),
                "negative": sent.get("negative_pct"),
                "mentions": (r.payload or {}).get("volume_trends", {}).get("total_mentions"),
            }
        )
    return series

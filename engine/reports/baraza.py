"""The Baraza Index: what ordinary Kenyans said about their leaders this week.

A public, shareable weekly leaderboard across ALL tracked politicians —
ranked by net sentiment over the last 7 days, with week-over-week movement
and one real "quote of the week" each. Pure aggregation, no LLM cost, safe
to serve unauthenticated (public figures, public statements, no PII beyond
public handles).
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from engine.db.models import MentionSentiment, Politician, RawMention


def _window_stats(db: Session, politician_id: str, start: datetime, end: datetime) -> dict:
    rows = (
        db.query(RawMention, MentionSentiment)
        .outerjoin(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
        .filter(
            RawMention.politician_id == politician_id,
            RawMention.posted_at >= start,
            RawMention.posted_at < end,
            RawMention.is_spam == 0,
        )
        .all()
    )
    scored = [s.sentiment for _, s in rows if s is not None]
    pos = sum(1 for x in scored if x == "positive")
    neg = sum(1 for x in scored if x == "negative")
    net = (100.0 * (pos - neg) / len(scored)) if scored else None

    def engagement(m: RawMention) -> int:
        e = m.engagement_json or {}
        return sum(int(e.get(k) or 0) for k in ("views", "likes", "shares", "comments"))

    quote = None
    with_text = [(m, s) for m, s in rows if (m.text or "").strip()]
    # Quote of the week: highest-engagement citizen voice — comments first.
    ranked = sorted(
        with_text,
        key=lambda pair: (pair[0].source_type == "comment", engagement(pair[0])),
        reverse=True,
    )
    if ranked:
        m, s = ranked[0]
        quote = {
            "text": (m.text or "")[:240],
            "author": m.author_handle,
            "platform": m.platform,
            "sentiment": s.sentiment if s else None,
            "url": m.source_url,
        }
    return {"mentions": len(rows), "scored": len(scored), "net_sentiment": net, "quote": quote}


def build_baraza_index(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    this_week = (now - timedelta(days=7), now)
    last_week = (now - timedelta(days=14), now - timedelta(days=7))

    entries = []
    for politician in db.query(Politician).all():
        cur = _window_stats(db, politician.id, *this_week)
        if cur["mentions"] == 0:
            continue
        prev = _window_stats(db, politician.id, *last_week)
        movement = None
        if cur["net_sentiment"] is not None and prev["net_sentiment"] is not None:
            movement = round(cur["net_sentiment"] - prev["net_sentiment"], 1)
        entries.append(
            {
                "name": politician.name,
                "mentions_this_week": cur["mentions"],
                "net_sentiment": round(cur["net_sentiment"], 1) if cur["net_sentiment"] is not None else None,
                "movement_vs_last_week": movement,
                "quote_of_the_week": cur["quote"],
            }
        )
    entries.sort(key=lambda e: (e["net_sentiment"] is not None, e["net_sentiment"] or -999), reverse=True)
    for rank, entry in enumerate(entries, 1):
        entry["rank"] = rank
    return {
        "title": "The Baraza Index",
        "subtitle": "What Kenyans said about their leaders this week",
        "week_ending": now.date().isoformat(),
        "entries": entries,
    }

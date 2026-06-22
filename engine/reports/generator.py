from collections import Counter
from datetime import datetime


def generate_report_payload(
    politician_name: str,
    window_start: datetime,
    window_end: datetime,
    mentions: list[dict],
    sentiments: dict[str, dict],
    narratives: list[dict],
    influence_ranking: list[dict],
    network_snapshot: dict,
) -> dict:
    sentiment_counts = Counter(s["sentiment"] for s in sentiments.values())
    total_sentiment = sum(sentiment_counts.values()) or 1

    volume_by_platform = Counter(m["platform"] for m in mentions)
    volume_by_day = Counter(m["posted_at"].date().isoformat() for m in mentions)

    dominant_sentiment = sentiment_counts.most_common(1)[0][0] if sentiment_counts else "neutral"
    top_narrative = max(narratives, key=lambda n: n["strength_score"])["label"] if narratives else "none"

    executive_summary = (
        f"In the period {window_start.date()} to {window_end.date()}, {politician_name} had "
        f"{len(mentions)} mentions across {len(volume_by_platform)} platforms. "
        f"Overall sentiment was predominantly {dominant_sentiment}. "
        f"The leading narrative was '{top_narrative}'."
    )

    return {
        "executive_summary": executive_summary,
        "sentiment_breakdown": {
            "positive_pct": round(100 * sentiment_counts.get("positive", 0) / total_sentiment, 1),
            "neutral_pct": round(100 * sentiment_counts.get("neutral", 0) / total_sentiment, 1),
            "negative_pct": round(100 * sentiment_counts.get("negative", 0) / total_sentiment, 1),
            "total_mentions_analyzed": total_sentiment,
        },
        "volume_trends": {
            "by_platform": dict(volume_by_platform),
            "by_day": dict(sorted(volume_by_day.items())),
            "total_mentions": len(mentions),
        },
        "influence_summary": influence_ranking,
        "narrative_breakdown": [
            {
                "label": n["label"],
                "description": n["description"],
                "strength_score": round(n["strength_score"], 2),
                "growth_rate": round(n["growth_rate"], 2),
                "mention_count": len(n["mention_ids"]),
            }
            for n in sorted(narratives, key=lambda n: n["strength_score"], reverse=True)
        ],
        "network_insights": network_snapshot,
    }

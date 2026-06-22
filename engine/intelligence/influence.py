from collections import defaultdict


def score_influence(mentions: list[dict], sentiments: dict[str, dict]) -> list[dict]:
    """Ranks authors by mention volume, engagement-cascade size, and sentiment-direction
    contribution. `sentiments` maps mention_id -> {"sentiment": ..., "intensity": ...}.

    Returns the top drivers sorted by score, descending.
    """
    by_author: dict[str, list[dict]] = defaultdict(list)
    for mention in mentions:
        by_author[mention["author_handle"]].append(mention)

    scored = []
    for author, author_mentions in by_author.items():
        volume = len(author_mentions)
        cascade = sum(
            m["engagement"].get("shares", 0) + m["engagement"].get("comments", 0) for m in author_mentions
        )

        sentiment_contribution = 0.0
        for m in author_mentions:
            s = sentiments.get(m["id"])
            if not s:
                continue
            direction = {"positive": 1, "negative": -1, "neutral": 0}[s["sentiment"]]
            sentiment_contribution += direction * s["intensity"]

        score = volume * 1.0 + cascade * 0.5 + abs(sentiment_contribution) * 0.3
        scored.append(
            {
                "author_handle": author,
                "volume": volume,
                "cascade_size": cascade,
                "sentiment_contribution": sentiment_contribution,
                "score": score,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:10]

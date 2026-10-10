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

    # Impact: how much of the conversation this account actually accounts for.
    #
    # `score` above is a weighted sum of raw counts, so it is unbounded and
    # means nothing on its own — "influence 15.0" is not comparable between
    # two subjects, two windows, or even two accounts without knowing the
    # totals. A reader asked to rank by it has to be told what good looks
    # like, which defeats ranking.
    #
    # Impact is defined as the client defines it: coverage plus engagement.
    # Each as a SHARE of the run's own total, so the pair are on one scale
    # and add to something meaningful — an account with 40% of the coverage
    # and 60% of the engagement has an impact of 50, and that number means
    # the same thing in every report.
    total_volume = sum(row["volume"] for row in scored) or 1
    total_cascade = sum(row["cascade_size"] for row in scored) or 1
    for row in scored:
        coverage_share = 100 * row["volume"] / total_volume
        engagement_share = 100 * row["cascade_size"] / total_cascade
        row["coverage_share"] = round(coverage_share, 1)
        row["engagement_share"] = round(engagement_share, 1)
        row["impact"] = round((coverage_share + engagement_share) / 2, 1)
        # Bands for grouping. A ranked list of ten is still ten things to
        # read; three groups is a glance. The thresholds are shares of the
        # conversation, not percentiles — a run where nobody dominates
        # should show nobody in the top band rather than promoting whoever
        # came first.
        row["impact_band"] = ("high" if row["impact"] >= 20
                              else "medium" if row["impact"] >= 7
                              else "low")

    scored.sort(key=lambda x: (x["impact"], x["score"]), reverse=True)
    return scored[:10]

import json
from collections import Counter
from datetime import datetime

from engine import llm, stages

TOP_QUOTES = 8

SUMMARY_PROMPT = """You are writing the executive summary of a political intelligence report about {name} for the period {start} to {end}.

Below are the precomputed statistics for the period, as JSON. Write 3-5 sentences summarizing them for a campaign strategist: overall visibility, sentiment direction, leading narratives, and who is driving the conversation. Use ONLY numbers that appear in the statistics — do not invent or extrapolate any figure.

Statistics:
{stats}

The required JSON shape is: {{"summary": "the executive summary text"}}"""


def corpus_preview(mentions: list[dict], window_end) -> dict:
    """The report as it stands before a single model call.

    Volume, platform spread, recency and the loudest mentions are all pure
    arithmetic over rows we already hold. None of it needs the LLM, and all of
    it is genuinely useful — yet it used to arrive only at the END of the
    longest stretch of a run, because it was computed alongside sentiment and
    narratives rather than before them.

    So a reader waited through entity linking, people extraction and
    whole-corpus sentiment scoring to be told how many mentions there were.
    This lets that land in seconds instead.

    Sentiment/narrative/influence keys are present but EMPTY, not zeroed: the
    payload has to keep the shape the frontend reads, and a `None` renders as
    "—" (not yet known) where a 0 would assert that nothing is positive.
    """
    volume_by_platform = Counter(m["platform"] for m in mentions)
    volume_by_day = Counter(
        m["posted_at"].date().isoformat() for m in mentions if m.get("posted_at")
    )
    volume_by_source_type = Counter(m["source_type"] for m in mentions if m.get("source_type"))
    volume_by_language = Counter(m["language"] for m in mentions if m.get("language"))

    def _age_days(m):
        posted = m.get("posted_at")
        return (window_end - posted).days if posted else None

    return {
        "sentiment_breakdown": {
            "positive_pct": None,
            "neutral_pct": None,
            "negative_pct": None,
            "total_mentions_analyzed": 0,
        },
        "volume_trends": {
            "by_platform": dict(volume_by_platform),
            "by_day": dict(sorted(volume_by_day.items())),
            "by_source_type": dict(volume_by_source_type),
            "by_language": dict(volume_by_language),
            "total_mentions": len(mentions),
            "last_7_days": sum(1 for m in mentions if (_age_days(m) or 999) < 7),
            "last_30_days": sum(1 for m in mentions if (_age_days(m) or 999) < 30),
        },
        "influence_summary": [],
        "narrative_breakdown": [],
        "network_insights": {},
        "notable_mentions": _notable_mentions(mentions, {}),
        "executive_summary": "",
    }


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
    # The true count, and separately a divisor that cannot be zero. These were
    # one variable: `sum(...) or 1`. The guard leaked into the payload, so a run
    # that scored NOTHING reported "1 analysed" and a 0.0% sentiment score —
    # presenting a total scoring failure as a real reading of the subject.
    analysed = sum(sentiment_counts.values())
    divisor = analysed or 1

    volume_by_platform = Counter(m["platform"] for m in mentions)
    volume_by_day = Counter(m["posted_at"].date().isoformat() for m in mentions)
    volume_by_source_type = Counter(m["source_type"] for m in mentions if m.get("source_type"))
    volume_by_language = Counter(m["language"] for m in mentions if m.get("language"))

    dominant_sentiment = sentiment_counts.most_common(1)[0][0] if sentiment_counts else "neutral"
    top_narrative = max(narratives, key=lambda n: n["strength_score"])["label"] if narratives else "none"

    payload = {
        "sentiment_breakdown": {
            # None, not 0.0, when nothing was scored. A zero asserts "none of
            # this is positive"; None renders as "—" and says we do not know.
            "positive_pct": round(100 * sentiment_counts.get("positive", 0) / divisor, 1) if analysed else None,
            "neutral_pct": round(100 * sentiment_counts.get("neutral", 0) / divisor, 1) if analysed else None,
            "negative_pct": round(100 * sentiment_counts.get("negative", 0) / divisor, 1) if analysed else None,
            "total_mentions_analyzed": analysed,
            # Scored out of eligible, so a silent scoring failure is a visible
            # number rather than an invented percentage.
            "coverage_pct": round(100 * analysed / max(len(mentions), 1), 1),
        },
        "volume_trends": {
            "by_platform": dict(volume_by_platform),
            "by_day": dict(sorted(volume_by_day.items())),
            "by_source_type": dict(volume_by_source_type),
            "by_language": dict(volume_by_language),
            "total_mentions": len(mentions),
            # Recency-first callouts: a live monitoring product leads with
            # what's happening now, not the historical window total.
            "last_7_days": sum(1 for m in mentions if (window_end - m["posted_at"]).days < 7),
            "last_30_days": sum(1 for m in mentions if (window_end - m["posted_at"]).days < 30),
        },
        "influence_summary": influence_ranking,
        "narrative_breakdown": [
            {
                "label": n["label"],
                "description": n["description"],
                "strength_score": round(n["strength_score"], 2),
                "growth_rate": round(n["growth_rate"], 2),
                "mention_count": len(n["mention_ids"]),
                # Carried, not summarised away: the page has to be able to open
                # a narrative and show the posts it was built from.
                "evidence": n.get("evidence", []),
                "labelled_by": n.get("labelled_by", "model"),
            }
            for n in sorted(narratives, key=lambda n: n["strength_score"], reverse=True)
        ],
        "network_insights": network_snapshot,
        "notable_mentions": _notable_mentions(mentions, sentiments),
    }

    payload["executive_summary"] = _executive_summary(
        politician_name, window_start, window_end, payload, len(mentions), len(volume_by_platform),
        dominant_sentiment, top_narrative,
    )
    return payload


def _notable_mentions(mentions: list[dict], sentiments: dict[str, dict]) -> list[dict]:
    """Highest-engagement mentions with their source URLs — evidence the
    report reader can click through and verify."""

    def engagement_score(m: dict) -> int:
        e = m.get("engagement") or {}
        return sum(int(e.get(k, 0) or 0) for k in ("likes", "shares", "comments", "views"))

    top = sorted(mentions, key=engagement_score, reverse=True)[:TOP_QUOTES]
    return [
        {
            "platform": m["platform"],
            "source_type": m.get("source_type"),
            "author_handle": m["author_handle"],
            "text": m["text"][:400],
            # Undated is a real state: archive pages and some feeds carry no
            # timestamp, and the corpus preview reads rows before any of the
            # dating fixups downstream. Crashing here would take the whole
            # early section with it.
            "posted_at": m["posted_at"].isoformat() if m.get("posted_at") else None,
            "source_url": m.get("source_url"),
            "engagement": m.get("engagement") or {},
            "sentiment": (sentiments.get(m["id"]) or {}).get("sentiment"),
        }
        for m in top
    ]


def _executive_summary(
    politician_name: str,
    window_start: datetime,
    window_end: datetime,
    payload: dict,
    total_mentions: int,
    platform_count: int,
    dominant_sentiment: str,
    top_narrative: str,
) -> str:
    """LLM summary constrained to precomputed stats; deterministic template
    fallback when the LLM is unavailable or returns garbage."""
    fallback = (
        f"In the period {window_start.date()} to {window_end.date()}, {politician_name} had "
        f"{total_mentions} mentions across {platform_count} platforms. "
        f"Overall sentiment was predominantly {dominant_sentiment}. "
        f"The leading narrative was '{top_narrative}'."
    )
    stats = {
        "sentiment_breakdown": payload["sentiment_breakdown"],
        "volume_trends": payload["volume_trends"],
        "narratives": payload["narrative_breakdown"][:5],
        "top_influencers": (payload["network_insights"] or {}).get("top_influencers", [])[:5],
        "top_people": (payload["network_insights"] or {}).get("top_people", [])[:5],
    }
    try:
        result = llm.call_json(
            SUMMARY_PROMPT.format(
                name=politician_name,
                start=window_start.date(),
                end=window_end.date(),
                stats=json.dumps(stats, default=str),
            ),
            max_tokens=512,
        )
        summary = result.get("summary") if isinstance(result, dict) else None
        return summary.strip() if summary else fallback
    except Exception as exc:  # noqa: BLE001
        # The executive brief is the first thing anyone reads. Falling back to
        # the template sentence without a trace is why "In the period X, Y had
        # N mentions… the leading narrative was 'narrative-3'" kept appearing
        # as though it were the analysis.
        stages.current().failed("executive_summary", exc)
        return fallback

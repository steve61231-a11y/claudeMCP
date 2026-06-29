"""Report sections written by dedicated, single-purpose LLM calls rather than one
call doing everything. Each function below is its own "tool" — narrow prompt, narrow
job — and `enrich_report_payload` fans them out concurrently so the extra depth
doesn't add latency (they don't depend on each other, only on the already-computed
rule-based payload from `generate_report_payload`).
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from engine import llm


def _context_blob(
    politician_name: str,
    window_start: datetime,
    window_end: datetime,
    sentiment_breakdown: dict,
    volume_trends: dict,
    influence_summary: list[dict],
    narrative_breakdown: list[dict],
) -> str:
    return json.dumps(
        {
            "politician": politician_name,
            "window": f"{window_start.date()} to {window_end.date()}",
            "sentiment": sentiment_breakdown,
            "volume": {
                "total_mentions": volume_trends["total_mentions"],
                "by_platform": volume_trends["by_platform"],
            },
            "narratives": [
                {
                    "label": n["label"],
                    "description": n["description"],
                    "strength_score": n["strength_score"],
                    "growth_rate": n["growth_rate"],
                    "mention_count": n["mention_count"],
                }
                for n in narrative_breakdown[:8]
            ],
            "top_influence_drivers": [
                {
                    "handle": i["author_handle"],
                    "score": round(i["score"], 1),
                    "sentiment_contribution": round(i["sentiment_contribution"], 1),
                }
                for i in influence_summary[:8]
            ],
        },
        default=str,
    )


SUMMARY_PROMPT = """You are a political intelligence analyst writing the executive summary
for a reputation/sentiment report. Use only the structured data below — don't invent facts.

Data:
{context}

Write a 4-6 sentence executive summary covering overall sentiment, the dominant narrative(s),
volume/platform spread, and the single most important takeaway for the politician's team.

Respond with ONLY a JSON object: {{"summary": "..."}}
"""

RISKS_PROMPT = """You are a political intelligence analyst identifying reputation risks
from the structured data below — don't invent facts.

Data:
{context}

List 3-5 concrete reputation risks visible in this data (e.g. a growing negative narrative,
a high-influence critic, a platform where sentiment is notably worse). Each should be one
specific sentence, not generic advice.

Respond with ONLY a JSON object: {{"risks": ["...", "..."]}}
"""

OPPORTUNITIES_PROMPT = """You are a political intelligence analyst identifying opportunities
from the structured data below — don't invent facts.

Data:
{context}

List 3-5 concrete opportunities visible in this data (e.g. a positive narrative gaining
traction, a supportive influencer worth amplifying, an underused platform). Each should be
one specific sentence, not generic advice.

Respond with ONLY a JSON object: {{"opportunities": ["...", "..."]}}
"""

TRENDS_PROMPT = """You are a political intelligence analyst flagging emerging trends to watch
from the structured data below — don't invent facts.

Data:
{context}

List 3-5 emerging trends worth monitoring going forward (e.g. a narrative with high growth
rate even if not yet dominant, an emerging platform shift). Each should be one specific
sentence, not generic advice.

Respond with ONLY a JSON object: {{"trends": ["...", "..."]}}
"""


def generate_executive_summary(context: str) -> str:
    result = llm.call_json(SUMMARY_PROMPT.format(context=context), max_tokens=400)
    return result.get("summary", "")


def generate_risks(context: str) -> list[str]:
    result = llm.call_json(RISKS_PROMPT.format(context=context), max_tokens=500)
    return result.get("risks", [])


def generate_opportunities(context: str) -> list[str]:
    result = llm.call_json(OPPORTUNITIES_PROMPT.format(context=context), max_tokens=500)
    return result.get("opportunities", [])


def generate_trends(context: str) -> list[str]:
    result = llm.call_json(TRENDS_PROMPT.format(context=context), max_tokens=500)
    return result.get("trends", [])


def enrich_report_payload(
    politician_name: str,
    window_start: datetime,
    window_end: datetime,
    payload: dict,
) -> dict:
    """Runs the four section-writer calls above concurrently and merges their
    output into `payload`. Each section fails independently — one LLM hiccup
    falls back to the existing rule-based value (or an empty list) instead of
    taking down the whole report.
    """
    context = _context_blob(
        politician_name,
        window_start,
        window_end,
        payload["sentiment_breakdown"],
        payload["volume_trends"],
        payload["influence_summary"],
        payload["narrative_breakdown"],
    )

    jobs = {
        "executive_summary": (generate_executive_summary, payload["executive_summary"]),
        "risks": (generate_risks, []),
        "opportunities": (generate_opportunities, []),
        "trends": (generate_trends, []),
    }

    def run(key):
        fn, fallback = jobs[key]
        try:
            return key, fn(context)
        except Exception:
            return key, fallback

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for key, value in pool.map(run, jobs):
            payload[key] = value

    return payload

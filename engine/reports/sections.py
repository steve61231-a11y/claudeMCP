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
from engine.reports import analysts


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
    mentions: list[dict] | None = None,
    narratives: list[dict] | None = None,
) -> dict:
    """Runs every section writer concurrently and merges the output into
    `payload`. Each section fails independently — one LLM hiccup falls back to
    the existing rule-based value (or an empty value) instead of taking down
    the whole report.

    When `mentions` (full-text corpus dicts, incl. comments) and `narratives`
    (with mention_ids) are provided, the corpus-reading analyst sections are
    generated too: public_voice, platform_pulse, timeline, influencer_stances,
    narrative_deep_dives, and the executive_brief synthesizer. Without them
    (older callers/tests), only the aggregate-based sections run.
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
        "executive_summary": (lambda: generate_executive_summary(context), payload["executive_summary"]),
        "risks": (lambda: generate_risks(context), []),
        "opportunities": (lambda: generate_opportunities(context), []),
        "trends": (lambda: generate_trends(context), []),
    }

    if mentions:
        by_day = payload["volume_trends"].get("by_day", {})
        influence = payload["influence_summary"]
        mentions_by_id = {str(m.get("id")): m for m in mentions}
        # Whole-corpus map-reduce digest: guarantees every mention is read by
        # the model (not a truncated slice) and powers the deep-insight pass.
        from engine.reports.digest import build_corpus_digest

        corpus_digest = build_corpus_digest(politician_name, mentions)
        payload["coverage"] = corpus_digest["coverage"]
        jobs["deep_insights"] = (
            lambda: analysts.analyze_deep_insights(politician_name, corpus_digest),
            {"insights": [], "the_one_thing": ""},
        )
        jobs.update(
            {
                "public_voice": (
                    lambda: analysts.analyze_public_voice(politician_name, mentions),
                    {},
                ),
                "platform_pulse": (
                    lambda: analysts.analyze_platform_pulse(politician_name, mentions),
                    [],
                ),
                "timeline": (
                    lambda: analysts.analyze_timeline(politician_name, mentions, by_day),
                    [],
                ),
                "influencer_stances": (
                    lambda: analysts.analyze_influencer_stances(politician_name, mentions, influence),
                    [],
                ),
            }
        )
        if narratives:
            jobs["narrative_deep_dives"] = (
                lambda: analysts.analyze_narrative_deep_dives(politician_name, narratives, mentions_by_id),
                [],
            )

    def run(key):
        fn, fallback = jobs[key]
        try:
            return key, fn()
        except Exception:
            return key, fallback

    from engine.config import settings

    _cap = 3 if settings.low_memory else 8
    with ThreadPoolExecutor(max_workers=min(_cap, len(jobs))) as pool:
        for key, value in pool.map(run, jobs):
            payload[key] = value

    if mentions:
        # Synthesizer reads every analyst's output ("the major AI that
        # analyzes all of that"), then the grounding verifier strips any
        # unsupported biographical/status claims from the free-prose sections.
        analyst_outputs = {
            k: payload.get(k)
            for k in (
                "public_voice",
                "platform_pulse",
                "timeline",
                "influencer_stances",
                "narrative_deep_dives",
                "deep_insights",
                "sentiment_breakdown",
            )
        }
        try:
            payload["executive_brief"] = analysts.synthesize_executive_brief(politician_name, analyst_outputs)
        except Exception:
            payload["executive_brief"] = payload.get("executive_summary", "")

        source_quotes = _collect_quotes(payload)
        prose = {
            "executive_brief": payload.get("executive_brief", ""),
            "executive_summary": payload.get("executive_summary", ""),
        }
        cleaned = analysts.verify_grounding(prose, source_quotes)
        payload.update(cleaned)

    return payload


def _collect_quotes(payload: dict) -> list[str]:
    quotes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if "text" in node and "ref" in node:
                quotes.append(f"@{node.get('author')} ({node.get('platform')}): {node['text']}")
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for key in ("public_voice", "platform_pulse", "timeline", "influencer_stances", "narrative_deep_dives"):
        walk(payload.get(key))
    return quotes

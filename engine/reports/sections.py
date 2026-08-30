"""Report sections written by dedicated, single-purpose LLM calls rather than one
call doing everything. Each function below is its own "tool" — narrow prompt, narrow
job — and `enrich_report_payload` fans them out concurrently so the extra depth
doesn't add latency (they don't depend on each other, only on the already-computed
rule-based payload from `generate_report_payload`).
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from engine import llm, stages
from engine.reports import analysts

# These four sections were capped at 400-500 tokens, so the cap — not the
# evidence — decided how much they said. They read aggregate statistics, so
# they are cheap; there is no reason for them to be the thinnest part of the
# report.
SECTION_MAX_TOKENS = 3000


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
                for n in narrative_breakdown[:20]
            ],
            "top_influence_drivers": [
                {
                    "handle": i["author_handle"],
                    "score": round(i["score"], 1),
                    "sentiment_contribution": round(i["sentiment_contribution"], 1),
                }
                for i in influence_summary[:20]
            ],
        },
        default=str,
    )


SUMMARY_PROMPT = """You are a political intelligence analyst writing the executive summary
for a reputation/sentiment report. Use only the structured data below — don't invent facts.

Data:
{context}

Write a 400-600 word executive summary covering overall sentiment and how it is distributed,
each of the dominant narratives and what is driving it, volume and platform spread and what the
differences between platforms mean, the accounts doing the most to shape it, and the most
important takeaway for the politician's team. Specific throughout — name the narratives, the
platforms and the handles. No generic commentary.

Respond with ONLY a JSON object: {{"summary": "..."}}
"""

RISKS_PROMPT = """You are a political intelligence analyst identifying reputation risks
from the structured data below — don't invent facts.

Data:
{context}

List every concrete reputation risk visible in this data (e.g. a growing negative narrative,
a high-influence critic, a platform where sentiment is notably worse) — typically 6-12 where the
data supports it, not three. Each is 2-4 sentences: what the risk is, what in the data shows it,
and why it matters. Specific, not generic advice.

Respond with ONLY a JSON object: {{"risks": ["...", "...", "...", "...", "...", "..."]}}
"""

OPPORTUNITIES_PROMPT = """You are a political intelligence analyst identifying opportunities
from the structured data below — don't invent facts.

Data:
{context}

List every concrete opportunity visible in this data (e.g. a positive narrative gaining
traction, a supportive influencer worth amplifying, an underused platform) — typically 6-12 where
the data supports it, not three. Each is 2-4 sentences: what the opening is, what in the data
shows it, and what acting on it would look like. Specific, not generic advice.

Respond with ONLY a JSON object: {{"opportunities": ["...", "...", "...", "...", "...", "..."]}}
"""

TRENDS_PROMPT = """You are a political intelligence analyst flagging emerging trends to watch
from the structured data below — don't invent facts.

Data:
{context}

List every emerging trend worth monitoring going forward (e.g. a narrative with high growth
rate even if not yet dominant, an emerging platform shift) — typically 6-12 where the data
supports it, not three. Each is 2-4 sentences: what is moving, the numbers that show it, and
where it goes if it continues. Specific, not generic advice.

Respond with ONLY a JSON object: {{"trends": ["...", "...", "...", "...", "...", "..."]}}
"""


def generate_executive_summary(context: str) -> str:
    result = llm.call_json(SUMMARY_PROMPT.format(context=context), max_tokens=SECTION_MAX_TOKENS)
    return result.get("summary", "")


def generate_risks(context: str) -> list[str]:
    result = llm.call_json(RISKS_PROMPT.format(context=context), max_tokens=SECTION_MAX_TOKENS)
    return result.get("risks", [])


def generate_opportunities(context: str) -> list[str]:
    result = llm.call_json(OPPORTUNITIES_PROMPT.format(context=context), max_tokens=SECTION_MAX_TOKENS)
    return result.get("opportunities", [])


def generate_trends(context: str) -> list[str]:
    result = llm.call_json(TRENDS_PROMPT.format(context=context), max_tokens=SECTION_MAX_TOKENS)
    return result.get("trends", [])


def enrich_report_payload(
    politician_name: str,
    window_start: datetime,
    window_end: datetime,
    payload: dict,
    mentions: list[dict] | None = None,
    narratives: list[dict] | None = None,
    on_section=None,
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

    `on_section(key, value)` is called the moment each section lands, so a
    caller can stream sections to a waiting reader instead of holding the whole
    report back until the slowest analyst finishes. It must never raise; a
    publish failure is not allowed to cost the section.
    """
    def publish(key, value):
        if on_section is None:
            return
        try:
            on_section(key, value)
        except Exception:  # noqa: BLE001 — streaming is a courtesy, not a contract
            pass

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
        # THE seam: every analyst section passes through here, and a failure
        # used to return the fallback ([] or "") with no trace. An analyst that
        # died and a corpus with nothing to say produced the identical page.
        return key, stages.run_guarded(key, fn, fallback=fallback)

    from engine.config import settings

    if "coverage" in payload:
        publish("coverage", payload["coverage"])

    _cap = 3 if settings.low_memory else 8
    with ThreadPoolExecutor(max_workers=min(_cap, len(jobs))) as pool:
        # as_completed, not pool.map: map yields in submission order, so a
        # section that finished in 5 seconds waits behind one that takes four
        # minutes. Nothing downstream depends on the order, and a reader
        # watching the report build does.
        futures = [pool.submit(run, key) for key in jobs]
        for future in as_completed(futures):
            key, value = future.result()
            payload[key] = value
            publish(key, value)

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
        payload["executive_brief"] = stages.run_guarded(
            "executive_brief",
            lambda: analysts.synthesize_executive_brief(politician_name, analyst_outputs),
            fallback=payload.get("executive_summary", ""))

        source_quotes = _collect_quotes(payload)
        prose = {
            "executive_brief": payload.get("executive_brief", ""),
            "executive_summary": payload.get("executive_summary", ""),
        }
        cleaned = analysts.verify_grounding(prose, source_quotes)
        payload.update(cleaned)
        for key in cleaned:
            publish(key, payload[key])

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

"""Live demo API: wraps the real pipeline (engine.pipeline.run_pipeline) behind
a simple HTTP endpoint the front end can call directly.

Hardening knobs (engine/config.py): PULSE_API_KEY gates /api/report with an
X-API-Key header, ALLOWED_ORIGINS restricts CORS, and report submissions are
rate-limited per client IP. All default to permissive for local dev only.
"""

import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine.config import settings
from engine.db.models import MentionSentiment, Politician, RawMention
from engine.db.session import SessionLocal
from engine.intelligence import graph as graph_module


class _FakeGraphSession:
    def run(self, query, **params):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeDriver:
    def session(self):
        return _FakeGraphSession()


# Neo4j isn't reachable in this sandbox — fake the graph layer the same way
# engine/tests/test_pipeline.py and scripts/run_sifuna_real.py do, so the rest
# of the pipeline (Postgres, sentiment, influence, LLM) still runs for real.
graph_module.get_driver = lambda: _FakeDriver()
graph_module.get_network_snapshot = lambda politician_id, limit=50: {
    "politician_id": politician_id,
    "top_users": [],
}

from engine.pipeline import run_ingestion, run_pipeline  # noqa: E402  (import after monkeypatch)

app = FastAPI(title="Pulse live demo API")


def _scheduled_ingestion_loop() -> None:
    """Recurring ingestion sweep: fetch fresh mentions for every tracked
    politician so the stored corpus grows over time. Analysis stays
    request-time (it's incremental via RawMention.link_checked, so report
    requests only pay LLM costs for mentions added since the last report)."""
    interval = settings.ingestion_refresh_hours * 3600
    while True:
        time.sleep(interval)
        db = SessionLocal()
        try:
            # Pre-warm: names likely to be searched (cabinet, governors…)
            # are tracked automatically so they have data before anyone asks.
            for raw_name in settings.prewarm_names.split(","):
                if raw_name.strip():
                    _ensure_politician(db, raw_name.strip())
            for politician in db.query(Politician).all():
                try:
                    window_end = datetime.utcnow()
                    window_start = window_end - timedelta(days=7)
                    run_ingestion(db, politician, window_start, window_end,
                                  credit_budget=settings.scheduled_credit_budget)
                    from engine.intelligence.alerts import detect_alerts

                    detect_alerts(db, politician)
                except Exception:  # noqa: BLE001 — one politician failing must not stop the sweep
                    traceback.print_exc()
                    db.rollback()
        finally:
            db.close()


@app.on_event("startup")
def _start_scheduler() -> None:
    if settings.ingestion_refresh_hours > 0:
        threading.Thread(target=_scheduled_ingestion_loop, daemon=True).start()

_cors_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_api_key(x_api_key: str | None) -> None:
    """Enforced only when PULSE_API_KEY is configured, so local dev and the
    existing frontend keep working until the key is rolled out to both sides."""
    if settings.pulse_api_key and x_api_key != settings.pulse_api_key:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


# Per-IP sliding-window rate limit for report submissions (LLM cost / DoS guard).
_rate_hits: dict[str, list[float]] = {}


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    hits = [t for t in _rate_hits.get(client_ip, []) if now - t < 3600]
    if len(hits) >= settings.report_rate_limit_per_hour:
        raise HTTPException(status_code=429, detail="rate limit exceeded, try again later")
    hits.append(now)
    _rate_hits[client_ip] = hits


_JOB_TTL_SECONDS = 3600


def _evict_stale_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_SECONDS
    for job_id in [jid for jid, job in _jobs.items() if job.get("created_at", 0) < cutoff]:
        _jobs.pop(job_id, None)


class ReportRequest(BaseModel):
    name: str


def _ensure_politician(db, name: str) -> Politician:
    politician = db.query(Politician).filter_by(name=name).first()
    if politician:
        return politician

    last_word = name.strip().split()[-1]
    aliases = [name, f"Hon. {name}", f"Sen. {last_word}", f"Honorable {name}"]
    politician = Politician(name=name, aliases=aliases, keywords=[])
    db.add(politician)
    db.commit()
    return politician


def _extract_url(raw_payload: dict) -> str | None:
    """Best-effort link extraction — SocialCrawl's URL field location varies by
    surface (flat on brand-mentions, nested under `post` on TikTok/YouTube)."""
    if not isinstance(raw_payload, dict):
        return None
    fields = raw_payload.get("post") if isinstance(raw_payload.get("post"), dict) else raw_payload
    for key in ("url", "page_url", "link", "permalink"):
        value = fields.get(key) or raw_payload.get(key)
        if value:
            return value
    return None


def _build_frontend_payload(politician: Politician, report) -> dict:
    payload = report.payload
    sentiment = payload["sentiment_breakdown"]
    volume = payload["volume_trends"]

    db = SessionLocal()
    try:
        rows = (
            db.query(RawMention, MentionSentiment)
            .join(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
            .filter(RawMention.politician_id == politician.id)
            .order_by(RawMention.posted_at.desc())
            .limit(400)
            .all()
        )
    finally:
        db.close()

    def engagement_metric(mention: RawMention) -> tuple[int, str]:
        eng = mention.engagement_json or {}
        views = eng.get("views") or 0
        likes = eng.get("likes") or 0
        score = views + likes * 5
        label = f"{views} views" if views else (f"{likes} likes" if likes else "—")
        return score, label

    scored_mentions = []
    seen_urls: set[str] = set()
    for mention, sent in rows:
        url = _extract_url(mention.raw_payload)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        score, label = engagement_metric(mention)
        scored_mentions.append(
            {
                "platform": mention.platform,
                "metric": label,
                "sentiment": sent.sentiment,
                "headline": (mention.text or "")[:140],
                "url": url,
                "_score": score,
            }
        )
    scored_mentions.sort(key=lambda m: m["_score"], reverse=True)
    top_mentions = [{k: v for k, v in m.items() if k != "_score"} for m in scored_mentions[:10]]

    # Grassroots highlights: the most-engaged citizen comments, verbatim.
    db = SessionLocal()
    try:
        comment_rows = (
            db.query(RawMention, MentionSentiment)
            .join(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
            .filter(RawMention.politician_id == politician.id, RawMention.source_type == "comment")
            .order_by(RawMention.posted_at.desc())
            .limit(200)
            .all()
        )
    finally:
        db.close()
    top_comments = [
        {
            "platform": m.platform,
            "sentiment": s.sentiment,
            "author": m.author_handle,
            "text": (m.text or "")[:280],
        }
        for m, s in sorted(
            comment_rows, key=lambda pair: engagement_metric(pair[0])[0], reverse=True
        )[:12]
        if (m.text or "").strip()
    ]

    by_platform = sorted(volume["by_platform"].items(), key=lambda kv: kv[1], reverse=True)
    total_volume = volume["total_mentions"] or 1
    by_platform_named = [
        {"platform": p, "count": c, "share": round(100 * c / total_volume, 1)} for p, c in by_platform
    ]

    influence = [
        {
            "rank": i + 1,
            "score": round(item["score"], 1),
            "who": item["author_handle"],
            "platform": "",
            "note": f"{item['volume']} mention(s)",
            "sentiment": round(item["sentiment_contribution"], 1),
        }
        for i, item in enumerate(payload["influence_summary"][:10])
    ]

    network_nodes = [{"id": "subject", "label": politician.name, "group": "core", "value": 30}]
    network_edges = []
    insights = payload.get("network_insights") or {}
    top_people = insights.get("top_people") or []
    if top_people:
        # People-first map: named individuals co-mentioned with the subject
        # (with role/affiliation), influencers by reach, and real
        # person-to-person co-mention edges — not media-handle spokes.
        added: dict[str, str] = {}
        for person in top_people[:14]:
            node_id = person["name"].replace(" ", "_")
            added[person["name"]] = node_id
            group = "politician" if (person.get("role") or "") == "politician" else "person"
            label = person["name"] + (f" ({person['affiliation']})" if person.get("affiliation") else "")
            network_nodes.append(
                {"id": node_id, "label": label, "group": group, "value": max(6, person["co_mentions"] * 2)}
            )
            network_edges.append({"from": "subject", "to": node_id, "label": "co-mentioned"})
        for inf in (insights.get("top_influencers") or [])[:8]:
            node_id = f"inf_{inf['handle']}".replace(" ", "_")
            network_nodes.append(
                {
                    "id": node_id,
                    "label": f"{inf['handle']} ({inf.get('followers') or 0:,} followers)",
                    "group": "influencer",
                    "value": 10,
                }
            )
            network_edges.append({"from": node_id, "to": "subject", "label": "creates content about"})
        for edge in (insights.get("people_edges") or [])[:30]:
            if edge["from"] in added and edge["to"] in added:
                network_edges.append(
                    {"from": added[edge["from"]], "to": added[edge["to"]], "label": "appears together"}
                )
        legend = [
            {"group": "core", "label": "Subject", "color": "#7dd3fc"},
            {"group": "politician", "label": "Politician", "color": "#fbbf24"},
            {"group": "person", "label": "Public figure", "color": "#c4b5fd"},
            {"group": "influencer", "label": "Influencer (1k+ followers)", "color": "#86efac"},
        ]
    else:
        for item in payload["influence_summary"][:8]:
            node_id = item["author_handle"].replace(" ", "_")
            group = "rival" if item["sentiment_contribution"] < 0 else "ally"
            network_nodes.append(
                {"id": node_id, "label": item["author_handle"], "group": group, "value": max(6, item["volume"] * 3)}
            )
            network_edges.append({"from": "subject", "to": node_id, "label": "mentions"})
        legend = [
            {"group": "core", "label": "Subject", "color": "#7dd3fc"},
            {"group": "ally", "label": "Positive/neutral driver", "color": "#86efac"},
            {"group": "rival", "label": "Negative driver", "color": "#fb7185"},
        ]

    return {
        "name": politician.name,
        "title": "",
        "window": f"{report.window_start.date()} – {report.window_end.date()}",
        "generated": datetime.utcnow().date().isoformat(),
        "summary": payload["executive_summary"],
        "sentiment": {
            "negative": sentiment["negative_pct"],
            "neutral": sentiment["neutral_pct"],
            "positive": sentiment["positive_pct"],
            "totalAnalyzed": sentiment["total_mentions_analyzed"],
        },
        "volume": {
            "total": volume["total_mentions"],
            "platforms": len(volume["by_platform"]),
            "last72h": sum(
                c for d, c in volume["by_day"].items()
                if datetime.fromisoformat(d).date() >= (report.window_end.date() - timedelta(days=3))
            ),
            "byPlatform": by_platform_named,
            "engagement": {"youtubeViews": 0, "linkedinLikes": 0, "linkedinShares": 0, "linkedinComments": 0},
            "topMentions": top_mentions,
        },
        "influence": influence,
        "network": {
            "nodes": network_nodes,
            "edges": network_edges,
            "legend": legend,
        },
        "narratives": [
            {
                "label": n["label"],
                "strength": n["strength_score"],
                "growth": n["growth_rate"],
                "mentions": n["mention_count"],
                "description": n["description"],
            }
            for n in payload["narrative_breakdown"]
        ],
        "risks": payload.get("risks", []),
        "opportunities": payload.get("opportunities", []),
        "trends": payload.get("trends", []),
        # Corpus-grounded deep sections (empty when generated by older reports).
        "executiveBrief": payload.get("executive_brief", ""),
        "publicVoice": payload.get("public_voice", {}),
        "platformPulse": payload.get("platform_pulse", []),
        "timeline": payload.get("timeline", []),
        "influencerStances": payload.get("influencer_stances", []),
        "narrativeDeepDives": payload.get("narrative_deep_dives", []),
        "topComments": top_comments,
        "recency": {
            "last7Days": volume.get("last_7_days", 0),
            "last30Days": volume.get("last_30_days", 0),
        },
        "dataCoverage": payload.get("data_coverage", {}),
        "sinceLastReport": payload.get("since_last_report"),
        "sentimentHistory": payload.get("sentiment_history", []),
    }


# Report generation (LLM calls, entity-linking, sentiment) easily exceeds the
# request timeout of a free-tier hosting platform's load balancer, which kills
# the connection mid-flight and surfaces as a generic "Failed to fetch" in the
# browser. Run it in a background thread and have the frontend poll instead
# of holding one long-lived request open.
_jobs: dict[str, dict] = {}

# Pre-generated reports for names known in advance (e.g. a live demo audience),
# served instantly instead of risking the live pipeline crashing/OOMing on a
# memory-constrained hosting plan. Keyed by lowercased politician name.
_PRECACHED_REPORTS: dict[str, dict] = {
    "mbadi": {'name': 'Mbadi', 'title': '', 'window': '2025-12-04 – 2026-07-02', 'generated': '2026-07-02', 'summary': "Across 628 mentions analyzed between December 2025 and July 2026, Treasury CS John Mbadi's online sentiment is predominantly neutral (51.4%), with positive sentiment (26.4%) marginally outpacing negative (22.1%), suggesting a public still forming firm opinions on his tenure. The dominant narratives center on his presentation of the Sh4.8 trillion 2026/27 national budget — including high-profile proposals such as KSh 784.5 billion for education and KSh 9.4 billion for landless settlements — while his televised grilling by Jeff Koinange drew the highest narrative strength score (194.85), indicating that media scrutiny of his fiscal stewardship is the single most-watched storyline. Conversation is spread primarily across TikTok (174 mentions), Instagram (219), and LinkedIn (154), signaling that Mbadi commands a cross-demographic digital footprint spanning professional and youth audiences. Influence is concentrated among a handful of high-reach handles, with ntvkenya and _evansmwirigi carrying notably negative sentiment contributions (-6.0 and -20.0 respectively), suggesting that mainstream and digital media voices are pulling public perception downward. Peripheral but potentially damaging narratives — including corruption allegations, the village elders stipend controversy, and ODM internal clashes — remain low in volume but carry reputational risk if amplified. The single most important takeaway for Mbadi's team is that his media performance, particularly on live broadcast platforms, is the primary battleground for public trust, and proactive, well-prepared engagement with critical interviewers and influential digital voices is essential to consolidating the positive momentum his budget leadership narrative has begun to generate.", 'sentiment': {'negative': 22.1, 'neutral': 51.4, 'positive': 26.4, 'totalAnalyzed': 628}, 'volume': {'total': 628, 'platforms': 15, 'last72h': 56, 'byPlatform': [{'platform': 'instagram', 'count': 219, 'share': 34.9}, {'platform': 'tiktok', 'count': 174, 'share': 27.7}, {'platform': 'linkedin', 'count': 154, 'share': 24.5}, {'platform': 'youtube', 'count': 67, 'share': 10.7}, {'platform': 'news', 'count': 2, 'share': 0.3}, {'platform': 'reddit', 'count': 2, 'share': 0.3}, {'platform': 'www.tnx.africa', 'count': 2, 'share': 0.3}, {'platform': 'softpower.ug', 'count': 1, 'share': 0.2}, {'platform': 'hivileo.co.ke', 'count': 1, 'share': 0.2}, {'platform': 'newshub.co.ke', 'count': 1, 'share': 0.2}, {'platform': 'www.msymi.com', 'count': 1, 'share': 0.2}, {'platform': 'afrinewske.com', 'count': 1, 'share': 0.2}, {'platform': 'nairobiwire.com', 'count': 1, 'share': 0.2}, {'platform': 'blacknewsdaily.com', 'count': 1, 'share': 0.2}, {'platform': 'elefundcapital.com', 'count': 1, 'share': 0.2}], 'engagement': {'youtubeViews': 0, 'linkedinLikes': 0, 'linkedinShares': 0, 'linkedinComments': 0}, 'topMentions': [{'platform': 'tiktok', 'metric': '532505 views', 'sentiment': 'neutral', 'headline': 'Treasury CS John Mbadi to table a Ksh. 4.8 trillion budget statement before Parliament', 'url': 'https://www.tiktok.com/@citizen.digital/video/7650093524616088853'}, {'platform': 'tiktok', 'metric': '388573 views', 'sentiment': 'neutral', 'headline': 'CS Mbadi proposes Sh784.5 billion allocation to the Education sector, including Sh56.3 billion for HELB and Sh30.9 billion for university sc', 'url': 'https://www.tiktok.com/@ntvkenya/video/7650146636039015698'}, {'platform': 'tiktok', 'metric': '236496 views', 'sentiment': 'negative', 'headline': 'Highly trained hard faced SWAT commandos who can smell explosives 100km away were present keeping CS Mbadi safe, this are just ladies at wor', 'url': 'https://www.tiktok.com/@theofficial_deepstate/video/7650205137528556820'}, {'platform': 'instagram', 'metric': '170773 views', 'sentiment': 'neutral', 'headline': 'Treasury CS John Mbadi: "We Are Not Introducing Any Extra Charges That Are Going To Affect Money Transfers Through M-Pesa." 🎥 @nairobi.leo', 'url': 'https://www.instagram.com/reel/DYzcVHXoNPT/'}, {'platform': 'tiktok', 'metric': '154019 views', 'sentiment': 'negative', 'headline': 'Babu Owino grills Treasury CS John Mbadi. Says he demands immediate answers.', 'url': 'https://www.tiktok.com/@lightcasttvkenya/video/7645016748210621714'}, {'platform': 'tiktok', 'metric': '114475 views', 'sentiment': 'positive', 'headline': 'JEFF KOINANGE COINS A NEW THEME SONG FOR JOHN MBADI. MBADI BOY!!! WHATCHA GONNA DO? JEFF!!! 🤩🤩🤩', 'url': 'https://www.tiktok.com/@_evansmwirigi/video/7657633129502362888'}, {'platform': 'tiktok', 'metric': '127310 views', 'sentiment': 'negative', 'headline': 'Hakuna Siku LUOS watasupport Kikuyus tena!!Angry CS John Mbadi Vows in Kisumu Wabiro Mega Rally!', 'url': 'https://www.tiktok.com/@kenyanewstv/video/7646038932546653458'}, {'platform': 'tiktok', 'metric': '120842 views', 'sentiment': 'neutral', 'headline': 'How will the government plug the Ksh 1.1 trillion deficit for FY 2026/27? Treasury CS John Mbadi speaks on the need to increase the tax base', 'url': 'https://www.tiktok.com/@ntvkenya/video/7650022484946832656'}, {'platform': 'instagram', 'metric': '108945 views', 'sentiment': 'negative', 'headline': 'I had these questions for the Treasury CS John Mbadi. We demand immediate answers.', 'url': 'https://www.instagram.com/reel/DY5I4KUAZ7b/'}, {'platform': 'tiktok', 'metric': '107986 views', 'sentiment': 'negative', 'headline': 'Babu Owino has been schooled by Cs Mbadi😂', 'url': 'https://www.tiktok.com/@kasongo.tv2/video/7645351575719841045'}]}, 'influence': [{'rank': 1, 'score': 2623.0, 'who': 'money254hq', 'platform': '', 'note': '2 mention(s)', 'sentiment': 5.0}, {'rank': 2, 'score': 2331.3, 'who': 'ntvkenya', 'platform': '', 'note': '23 mention(s)', 'sentiment': -6.0}, {'rank': 3, 'score': 1590.0, 'who': '_evansmwirigi', 'platform': '', 'note': '9 mention(s)', 'sentiment': -20.0}, {'rank': 4, 'score': 892.3, 'who': 'michaelkamuren254', 'platform': '', 'note': '2 mention(s)', 'sentiment': -1.0}, {'rank': 5, 'score': 827.0, 'who': 'tiktoktrends_tk1', 'platform': '', 'note': '1 mention(s)', 'sentiment': 0.0}, {'rank': 6, 'score': 592.8, 'who': 'ajwang_junior', 'platform': '', 'note': '6 mention(s)', 'sentiment': -6.0}, {'rank': 7, 'score': 562.2, 'who': 'rtc_media', 'platform': '', 'note': '5 mention(s)', 'sentiment': -19.0}, {'rank': 8, 'score': 531.9, 'who': 'nextgen_254', 'platform': '', 'note': '1 mention(s)', 'sentiment': -3.0}, {'rank': 9, 'score': 429.5, 'who': 'topnewstvbackup', 'platform': '', 'note': '3 mention(s)', 'sentiment': 0.0}, {'rank': 10, 'score': 332.5, 'who': 'nairobileo.co.ke', 'platform': '', 'note': '2 mention(s)', 'sentiment': 0.0}], 'network': {'nodes': [{'id': 'subject', 'label': 'Mbadi', 'group': 'core', 'value': 30}, {'id': 'William_Ruto', 'label': 'William Ruto (Government of Kenya)', 'group': 'politician', 'value': 66}, {'id': 'Raila_Odinga', 'label': 'Raila Odinga (Orange Democratic Movement)', 'group': 'politician', 'value': 56}, {'id': 'Winnie_Odinga', 'label': 'Winnie Odinga (Orange Democratic Movement)', 'group': 'politician', 'value': 26}, {'id': 'Edwin_Sifuna', 'label': 'Edwin Sifuna (Orange Democratic Movement (ODM))', 'group': 'politician', 'value': 20}, {'id': 'Ndindi_Nyoro', 'label': 'Ndindi Nyoro (United Democratic Alliance)', 'group': 'politician', 'value': 18}, {'id': 'Sola_Yomi-Ajayi', 'label': 'Sola Yomi-Ajayi (Stragofin Advisory LLC)', 'group': 'person', 'value': 16}, {'id': 'Dahlia_Khalifa', 'label': 'Dahlia Khalifa (International Finance Corporation (IFC))', 'group': 'person', 'value': 14}, {'id': 'Sanyade_Okoli', 'label': 'Sanyade Okoli (Office of the President of Nigeria)', 'group': 'person', 'value': 14}, {'id': 'Ebru_Pakcan', 'label': 'Ebru Pakcan (Citigroup)', 'group': 'person', 'value': 14}, {'id': 'Nat_Adams', 'label': 'Nat Adams (Newmont Corporation)', 'group': 'person', 'value': 14}, {'id': 'Ruth_Odinga', 'label': 'Ruth Odinga (ODM)', 'group': 'politician', 'value': 14}, {'id': 'Wole_Famurewa', 'label': 'Wole Famurewa (U.S. Africa Exchange)', 'group': 'person', 'value': 14}, {'id': 'Uhuru_Kenyatta', 'label': 'Uhuru Kenyatta (Jubilee Party)', 'group': 'politician', 'value': 12}, {'id': 'Babu_Owino', 'label': 'Babu Owino (Orange Democratic Movement)', 'group': 'politician', 'value': 10}], 'edges': [{'from': 'subject', 'to': 'William_Ruto', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Raila_Odinga', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Winnie_Odinga', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Edwin_Sifuna', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Ndindi_Nyoro', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Sola_Yomi-Ajayi', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Dahlia_Khalifa', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Sanyade_Okoli', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Ebru_Pakcan', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Nat_Adams', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Ruth_Odinga', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Wole_Famurewa', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Uhuru_Kenyatta', 'label': 'co-mentioned'}, {'from': 'subject', 'to': 'Babu_Owino', 'label': 'co-mentioned'}, {'from': 'Sanyade_Okoli', 'to': 'Nat_Adams', 'label': 'appears together'}, {'from': 'Sanyade_Okoli', 'to': 'Dahlia_Khalifa', 'label': 'appears together'}, {'from': 'Sanyade_Okoli', 'to': 'Sola_Yomi-Ajayi', 'label': 'appears together'}, {'from': 'Wole_Famurewa', 'to': 'Ebru_Pakcan', 'label': 'appears together'}, {'from': 'Sanyade_Okoli', 'to': 'Wole_Famurewa', 'label': 'appears together'}, {'from': 'Dahlia_Khalifa', 'to': 'Wole_Famurewa', 'label': 'appears together'}, {'from': 'Sola_Yomi-Ajayi', 'to': 'Dahlia_Khalifa', 'label': 'appears together'}, {'from': 'Sola_Yomi-Ajayi', 'to': 'Wole_Famurewa', 'label': 'appears together'}, {'from': 'Sanyade_Okoli', 'to': 'Ebru_Pakcan', 'label': 'appears together'}, {'from': 'Sola_Yomi-Ajayi', 'to': 'Nat_Adams', 'label': 'appears together'}, {'from': 'Sola_Yomi-Ajayi', 'to': 'Ebru_Pakcan', 'label': 'appears together'}, {'from': 'Dahlia_Khalifa', 'to': 'Ebru_Pakcan', 'label': 'appears together'}, {'from': 'Dahlia_Khalifa', 'to': 'Nat_Adams', 'label': 'appears together'}, {'from': 'Nat_Adams', 'to': 'Ebru_Pakcan', 'label': 'appears together'}, {'from': 'Nat_Adams', 'to': 'Wole_Famurewa', 'label': 'appears together'}, {'from': 'Uhuru_Kenyatta', 'to': 'William_Ruto', 'label': 'appears together'}, {'from': 'Raila_Odinga', 'to': 'Ruth_Odinga', 'label': 'appears together'}, {'from': 'Raila_Odinga', 'to': 'William_Ruto', 'label': 'appears together'}, {'from': 'Raila_Odinga', 'to': 'Winnie_Odinga', 'label': 'appears together'}], 'legend': [{'group': 'core', 'label': 'Subject', 'color': '#7dd3fc'}, {'group': 'politician', 'label': 'Politician', 'color': '#fbbf24'}, {'group': 'person', 'label': 'Public figure', 'color': '#c4b5fd'}, {'group': 'influencer', 'label': 'Influencer (1k+ followers)', 'color': '#86efac'}]}, 'narratives': [{'label': 'Mbadi Grilled on TV', 'strength': 194.85, 'growth': 0.5, 'mentions': 5, 'description': 'Jeff Koinange subjects Finance Minister John Mbadi to tough on-air questioning over government spending, taxation, and political comparisons.'}, {'label': 'Budget Allocation Proposals', 'strength': 171.73, 'growth': 1.0, 'mentions': 3, 'description': 'CS John Mbadi has proposed significant budget allocations for the 2026/27 national budget, including KSh 9.4 billion for landless settlements and KSh 784.5 billion for the education sector.'}, {'label': 'Mbadi Budget Presentation', 'strength': 78.32, 'growth': 0.06, 'mentions': 33, 'description': 'Treasury CS John Mbadi made final preparations and traveled with heavy security from the National Treasury to Parliament to present the Sh4.8 trillion 2026/27 national budget.'}, {'label': 'Mbadi Budget Leadership', 'strength': 75.18, 'growth': 0.14, 'mentions': 15, 'description': "Social media users are praising Treasury CS John Mbadi's handling of the budget and tax reform agenda, with many commenters framing him as a capable, plain-speaking leader and even a future presidential contender."}, {'label': 'Village Elders Stipend Controversy', 'strength': 36.18, 'growth': 0.0, 'mentions': 8, 'description': "Treasury CS John Mbadi's proposal to allocate Ksh3.9 billion in stipends for village elders in the 2026/27 budget was met with heckling from MPs, sparking debate over government spending priorities."}, {'label': 'Mbadi Defends Economy', 'strength': 31.1, 'growth': 0.0, 'mentions': 10, 'description': "CS John Mbadi makes controversial public statements defending the government's economic record, including rising public debt, State House renovations, agricultural performance, and a slight dip in GDP growth."}, {'label': 'Mbadi ODM Leadership Clash', 'strength': 24.32, 'growth': 0.0, 'mentions': 6, 'description': "Treasury CS John Mbadi is publicly challenging ODM's internal power dynamics, calling for the removal of Secretary General Edwin Sifuna and rejecting the notion that the party is family property of the Odinga clan, amid broader wrangles over the party's future ahead of 2027."}, {'label': 'Mbadi Corruption Allegations', 'strength': 15.1, 'growth': 0.0, 'mentions': 4, 'description': 'Multiple posts accuse Treasury CS John Mbadi of financial misconduct, including diverting funds to ghost pensioners, misusing budget allocations, and being used by President Ruto for political schemes.'}, {'label': 'Budget and Controversies', 'strength': 9.98, 'growth': 0.33, 'mentions': 7, 'description': 'John Mbadi is in the spotlight for presenting a historic Sh4.8 trillion budget and the Finance Bill 2026, while also facing a public dispute with the Odinga family over ODM party leadership.'}, {'label': 'Budget Statement Delivery', 'strength': 9.57, 'growth': 0.5, 'mentions': 5, 'description': "Cabinet Secretary John Mbadi presented Kenya's FY 2026/27 Budget Statement on 11th June 2026, themed around sustaining the Bottom-Up Economic Transformation Agenda for resilient and inclusive growth."}, {'label': 'KRA Digital Tax Reform', 'strength': 6.74, 'growth': 1.0, 'mentions': 3, 'description': 'CS John Mbadi acknowledges challenges in tax administration due to the shift to the digital economy and signals upcoming KRA reforms aimed at improving revenue collection without increasing tax rates.'}, {'label': 'Mbadi Senate Grilling', 'strength': 6.0, 'growth': 0.0, 'mentions': 6, 'description': "CS John Mbadi faced tough questioning in the Senate, particularly from Senator Sifuna, in heated exchanges over economic planning and the President's nationwide tours."}, {'label': 'Mbadi vs Winnie Odinga', 'strength': 5.41, 'growth': 0.0, 'mentions': 4, 'description': 'Treasury CS John Mbadi publicly clashed with Winnie Odinga, dismissing her criticism and telling her to first win an elective seat before advising him, while also declaring his presidential ambitions.'}, {'label': 'PAYE Reform Fiscal Debate', 'strength': 3.0, 'growth': 1.0, 'mentions': 3, 'description': "Treasury CS John Mbadi is weighing potential PAYE band adjustments that could benefit lower-income earners but would create a KES 35 billion revenue gap, complicating Kenya's already strained fiscal position with a deficit exceeding targets."}], 'risks': ["The 'Mbadi Corruption Allegations' narrative, accusing him of diverting funds to ghost pensioners and serving as a political tool for President Ruto, carries a 15.1 strength score with 4 mentions and zero growth rate, suggesting a latent but persistent reputational threat that could re-ignite if new fiscal controversies emerge.", "High-influence critic @_evansmwirigi (influence score 1,590) is delivering a sentiment contribution of -20, making this single account one of the most damaging voices in Mbadi's online environment during this window.", "The 'Mbadi ODM Leadership Clash' narrative — publicly challenging ODM's internal power structure and the Odinga clan's grip on the party — risks alienating a core political base ahead of 2027 elections, with 6 mentions and zero growth rate indicating unresolved tension rather than a closed issue.", "TikTok is the second-largest platform by volume (174 mentions), and given the platform's youth-skewed, fast-amplifying nature, the combination of corruption allegations and economic defense controversies circulating there poses a disproportionate risk to Mbadi's public image relative to mention count alone.", "The 'Village Elders Stipend Controversy,' in which MPs openly heckled Mbadi's KSh 3.9 billion proposal, publicly signals legislative resistance to his budget authority and feeds into the broader negative narrative around government spending priorities, with 8 mentions and zero growth rate suggesting it has stalled but not been resolved."], 'opportunities': ["The 'Budget Allocation Proposals' narrative has a perfect 1.0 growth rate and covers high-impact spending like KSh 784.5 billion for education, making it the highest-momentum story to amplify with detailed explainer content before public scrutiny peaks.", "The 'Mbadi Budget Leadership' narrative shows organic social media users already framing Mbadi as a future presidential contender, and amplifying supporter handle 'money254hq' (score 2623, positive sentiment) could accelerate this presidential-viability framing ahead of 2027.", "LinkedIn has 154 mentions — the second-highest platform volume — yet budget and economic reform content targeted at Kenya's professional and business audience is underexploited relative to TikTok's 174 mentions, suggesting a strategic content push on LinkedIn to build credibility with economic influencers.", "The 'Mbadi ODM Leadership Clash' narrative (6 mentions, 0.0 growth) is currently low-volume and stalled, representing an early window to proactively shape the party-reform storyline before it gains negative momentum with the 2027 election cycle approaching.", "High-influence handle '_evansmwirigi' (score 1590, sentiment contribution -20) and 'rtc_media' (score 562, sentiment contribution -19) are driving significant negative sentiment and should be monitored and specifically counter-messaged with factual budget data before their narratives compound."], 'trends': ["The 'Budget Allocation Proposals' narrative holds the highest growth rate (1.0) despite only 3 mentions, signaling that Mbadi's specific spending proposals — including KSh 9.4 billion for landless settlements and KSh 784.5 billion for education — are gaining traction and could ignite sustained public debate as the 2026/27 budget is scrutinized.", "The 'Mbadi Budget Leadership' narrative is framing him as a future presidential contender among social media commenters, a presidential positioning signal worth tracking as 2027 campaign dynamics begin to crystallize.", "TikTok is the second-largest platform by volume (174 mentions), nearly matching Instagram (219), suggesting Mbadi's budget and political messaging is reaching a younger demographic that could shape populist narratives around him ahead of 2027.", "The 'Mbadi ODM Leadership Clash' narrative — specifically his public challenge to Edwin Sifuna and rejection of Odinga family ownership of ODM — has a non-zero strength score (24.32) and active mention count, making internal ODM fracture lines a key political risk to monitor.", 'Influence driver _evansmwirigi carries the third-highest influence score (1590.0) with a strongly negative sentiment contribution (-20.0), flagging this account as a concentrated source of reputational risk that warrants close tracking.'], 'executiveBrief': "POLITICAL INTELLIGENCE BRIEF: CS JOHN MBADI — PUBLIC SENTIMENT & STRATEGIC OUTLOOK\n\nWHAT THE PUBLIC IS SAYING AND FEELING\n\nThe dominant public mood is split along two fault lines that are pulling in opposite directions simultaneously. On fiscal policy, Mbadi has genuine popular capital — ordinary Kenyans and content creators are responding warmly to his plain-speaking communication style on taxes. @michaelkamuren254 (ref: 189d2a09) captures the sentiment directly: 'CS John Mbadi = The perfect Treasury CS ever. He explains the Finance Bill in simple words we ALL understand.' The PAYE threshold announcement (Ksh24,000 to Ksh30,000) landed well, and Mbadi's own framing — 'If the PAYE cuts we are rolling out are a campaign strategy, so be it. It's a good strategy. At the end of the day, it's putting money in people's pockets' (ref: 0906685e) — is being repeated approvingly rather than mocked. On LinkedIn, commenters are calling him a 'genius' and 'the best candidate to succeed Ruto in 2032' (ref: f8ba9c14).\n\nBut on the political side, the public feels something closer to alarm and betrayal — particularly among Luo and ODM-aligned audiences. The 'Where is that Raila' remark has become a phrase that crystallises a deeper anxiety: that Mbadi is actively dismantling the Odinga political legacy to serve his own ambitions. @swift.media.ke reports that Mama Ida Odinga herself has fired back, citing Mbadi's words — 'Wapi huyo baba yako saa hii atupee direction?' (ref: fc298ed7) — as the trigger. This is not a niche grievance. It is producing high-emotion, high-engagement content across TikTok and YouTube.\n\nTHE STORYLINES THAT MATTER AND WHERE THEY'RE HEADING\n\nThree narratives are live and accelerating.\n\nFirst, the ODM succession war is escalating toward a breaking point. Mbadi's public declaration — 'ODM does not belong to one family. Baba is no more, this is the post-Raila era' (ref: c49060c5) — combined with his move against Edwin Sifuna ('Some people must leave ODM party for it to remain intact... Mkinyamaza mtapata ODM imegawanywa mara tatu,' ref: eaf542da) and his dismissal of Winnie Odinga ('You have no capacity to advise me on how to run the ministry of finance. Be elected first,' ref: e0da2a0c) has opened a three-front war inside ODM. NTV Kenya is now headlining it as 'Mbadi versus the Odingas' (ref: 05b01772). Former Governor Evans Kidero is being cited by @kenyan.daily.repo alleging Ruto is 'using CS Mbadi to destroy Winnie Odinga' (ref: 82a5b036). This storyline is heading toward a formal ODM split unless it is actively managed.\n\nSecond, the 'accountability gap' narrative is gaining traction. Critics — including @shujaa.humphrey — are using his own history against him: 'Mbadi and Wandayi were loudest criticizing current government over high fuel prices while in opposition but currently are clueless defending Ruto' (ref: 30acc352). Separately, allegations that he 'admitted to lying and misleading Kenyans over the controversial 5 trillion shillings infrastructure fund' (ref: b98329ac) have not been rebutted publicly. Safina Party leader Jimmy Wanjigi is daring Mbadi 'to explain to Kenyans how Ksh 4 trillion borrowed by the government has been used' (ref: b5a890d0). These threads are currently scattered but could consolidate into a single 'Mbadi lied' narrative if a credible anchor — a senator, auditor, or major outlet — ties them together.\n\nThird, the 'presidential ambition' storyline is now fully public and carries risk. His statement 'I will come for your votes in 2032. I am the next President of Kenya' (ref: c0b670d0), alongside 'Am confident enough to go for the presidency in 2027' (ref: 8e60a9bb), is circulating widely. This feeds opponent framing that every policy decision — PAYE cuts included — is self-serving electioneering rather than genuine governance.\n\nWHO IS SHAPING OPINION\n\nFor Mbadi: @michaelkamuren254 (influence score 892) is the most vocal pro-Mbadi voice, amplifying destabilisation-plot narratives and Finance Bill praise. @money254hq (score 2,623) is the highest-reach account touching his portfolio — neutral in editorial stance but giving his PAYE announcements substantial oxygen. LinkedIn's professional layer (Money254, Business Daily Africa) is providing policy credibility.\n\nAgainst Mbadi: @_evansmwirigi (score 1,590) is the most damaging single account — clipping every moment of weakness from media appearances and framing them with gleeful mockery ('MBADI BOY!!! WHATCHA GONNA DO?' ref: a49929b3; 'JAMAA HASKII,' ref: 440e0f9c). @rtc_media is the sharpest loyalty-attack voice within the ODM base (ref: 55341d5a). @ntvkenya (score 2,331), while editorially neutral, is platforming the most damaging opposition quotes without pushback.\n\nCONCRETE MOMENTS THAT MOVED CONVERSATION\n\nJune 11 Budget Day (177 mentions) was the peak moment — the choking incident, MPs chanting 'Kunywa maji, expert' (ref: 055b6f0a), and the village elders heckling (ref: bcd71f1f) each went separately viral and collectively framed Mbadi as embattled rather than commanding. July 1 JKL appearance (28 mentions) extended that damage — Jeff Koinange's 'cooking' of Mbadi on national debt generated a second wave of mockery clips. His statement on that show — 'No country reduces its public debt, we can hit even 30 trillion in the future' (ref: ff4d4395) — is circulating as a standalone clip and will be weaponised.\n\nWHAT DESERVES ACTION THIS WEEK\n\nOne: The infrastructure fund lie allegation (ref: b98329ac) requires a specific, factual rebuttal before it anchors the broader 'Mbadi misled Kenyans' narrative. Silence is currently functioning as confirmation.\n\nTwo: The 'Where is that Raila' damage inside the Luo community is deepening daily. Mama Ida Odinga's public anger (ref: fc298ed7) signals this has moved beyond political commentary into personal grievance. A direct, non-defensive statement addressing the remark — not a retraction, but a contextualization — is needed before the June 2 Odinga family row (ref: 3d6f0b44) calcifies into a permanent base fracture.\n\nThree: The '30 trillion debt' clip from JKL is the most immediately exploitable soundbite in circulation. A proactive media appearance with a clear, simple counter-framing — before @_evansmwirigi and @rtc_media build a full narrative around it — is the single highest-return communications action available this week.", 'publicVoice': {'neutral': [{'theme': "Mbadi's ODM political ambitions and succession war reported as a major story", 'quotes': [{'ref': 'c49060c5', 'url': None, 'text': 'ODM does not belong to one family. Baba is no more, this is the post-Raila era - John Mbadi', 'author': 'kenyans.co.ke', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'c0b670d0', 'url': None, 'text': 'I will come for your votes in 2032. I am the next President of Kenya - John Mbadi #kenyanjournalistmedia #kenyansontiktok #sisindiosifuna #lindaground #mbadi #wanga', 'author': 'kenyanjournalistmedia.ke', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '05b01772', 'url': None, 'text': 'Mbadi versus the Odingas: Fresh political storm brewing after Mbadi launched an unusual attack against some members of the Odinga family over their influence in the party.', 'author': 'ntvkenya', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': "Observers and media report on Mbadi's declared ambitions, his clashes over ODM leadership, and his positioning in post-Raila party politics — without clearly taking sides."}, {'theme': 'Budget and Finance Bill announcements covered with mixed public reaction', 'quotes': [{'ref': 'eaf5d649', 'url': None, 'text': "😳📢 John Mbadi has left Kenyans stunned after today's budget reading! Some of the announcements have sparked intense reactions across the country. 🇰🇪🔥 #JohnMbadi #BudgetReading #KenyaNews #TrendingKenya #ViralVideo", 'author': 'genzdata7', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'dca51ef2', 'url': None, 'text': 'How will the government plug the Ksh 1.1 trillion deficit for FY 2026/27? Treasury CS John Mbadi speaks on the need to increase the tax base and challenges surrounding both external and domestic borrowing. #FixingTheNationNTV #Tiktokkenya', 'author': 'ntvkenya', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'bcd71f1f', 'url': None, 'text': 'MPs heckle CS Mbadi after proposing Ksh3.9 billion as stipend for village elders', 'author': 'kenyanske', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': 'Coverage of the budget presentation and Finance Bill 2026 draws significant public attention — some stunned, some curious — with reactions ranging from bewilderment to scrutiny rather than clear praise or condemnation.'}, {'theme': "Questions raised over Mbadi's relationship with President Ruto and his political loyalty", 'quotes': [{'ref': '401cafdf', 'url': None, 'text': 'JEFF KOINANGE LIVE: WAZIRI JOHN MBADI "TRIES TO MAKE RUTO" LOOK BETTER THAN UHURU KENYATTA. ATI MT. KENYA IS CONTENT? UONGO! 😒', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '82a5b036', 'url': None, 'text': 'DECLASSIFIED: Former Gov Evans KIDERO Exposes Why Ruto Is Using CS Mbadi To Destroy Winnie Odinga!!️ full video on youtube channel: Kenyan Daily Report Tv #ruto #mbadi #winnieodinga #luonyanza #kenyantiktok🇰🇪', 'author': 'kenyan.daily.repo', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': "Commenters and media note the tension around Mbadi's appointment and his positioning between Ruto's government and his ODM base, without a clearly positive or negative framing."}], 'critical': [{'theme': 'Accused of betraying and disrespecting Raila Odinga and the Odinga family', 'quotes': [{'ref': '55341d5a', 'url': None, 'text': "In simple terms, this is a clip of CS Mbadi saying Raila would not stop him from vying in any elective position after the 2022 elections no matter what he would do. Mbadi declared war on Raila long time ago...what makes you think he means well for Raila's party ODM! #fyp #foryou #foryoupage #kenyantiktok🇰🇪 #johnmbadi", 'author': 'rtc_media', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '7be58060', 'url': None, 'text': "'Where is that Raila' is the statement that has brought issues between Mbadi and the Odinga Family #fyp #fypシ #fypシ゚viral #viral_video #trending", 'author': 'topnewstvbackup', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'fc298ed7', 'url': None, 'text': 'Angry Mama Ida Odinga fires at Treasury CS John Mbadi over his recent remarks that were seen as disrespectful to Raila Odinga. "Wapi huyo baba yako saa hii atupee direction?" those were Mbadi\'s words while responding to Edwin Sifuna, remarks that angered many people, including Winnie Odinga.', 'author': 'swift.media.ke', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': "Commenters accuse Mbadi of mocking Raila Odinga, declaring war on his family, and being disloyal to the political figure who shaped his career. Anger is directed at his 'Where is that Raila' remark."}, {'theme': 'Accused of hypocrisy — criticized fuel prices in opposition but now defends them in government', 'quotes': [{'ref': '30acc352', 'url': None, 'text': "Citizen Tv doing the Lord's work to Treasury CS Mbadi and Energy CS Wandayi who were loudest criticizing current government over high fuel prices while in opposition but currently are clueless defending Ruto and waiting for his direction on hiked fuel prices #shujaahumphrey", 'author': 'shujaa.humphrey', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '04cb1c39', 'url': None, 'text': 'Mbadi, are we running a country or a Kanjo toilet?', 'author': 'he.babuowino', 'platform': 'instagram', 'source_type': 'video'}, {'ref': '64507580', 'url': None, 'text': 'CS John Mbadi exposed for using a fake Kenyan in Nairobi about Finance Bill #rejectfinancebill #johnmbadi #fakekenyans #ruto #financebill', 'author': 'tonywakaromo1', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': "Critics mock Mbadi for being 'clueless' and defending the very high fuel prices he loudly criticized when he was in opposition, calling him a hypocrite."}, {'theme': 'Accused of financial misconduct and misleading Kenyans on economic matters', 'quotes': [{'ref': 'b98329ac', 'url': None, 'text': 'Treasury Cabinet Secretary John Mbadi is on the spot after admitting to lying & misleading Kenyans over the controversial 5 trillion shillings infrastructure fund. #shujaahumphrey', 'author': 'shujaa.humphrey', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '41237e26', 'url': None, 'text': "BREAKING: Luos Speak Out! CS John Mbadi under fire the community demands real proof of empowerment. No more empty promises! Luos call on Orengo & Anyang' Nyong'o: Deliver real development NOW! #luo #kenyapolitics #accountabilitynow #kenyantiktok🇰🇪 #breakingnews", 'author': 'kisumotv', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': 'Critics allege Mbadi lied about the infrastructure fund, diverted funds to ghost pensioners, and has been caught out by auditors and opponents on economic facts.'}, {'theme': 'Mocked for angry outbursts and being lectured/grilled publicly by rivals', 'quotes': [{'ref': 'db34598d', 'url': None, 'text': 'Sifuna has successfully turned hon mbadi to a preacher 🇰🇪🤣 now preaching politics 😂⚡⚡🇰🇪🤣 #fyp #kenyantiktok🇰🇪 #nairobitiktokers #kikuyutiktok #luopeans♥️♥️🔥🔥', 'author': 'johnny.drycon1', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'f2e7eb77', 'url': None, 'text': 'Senator Sifuna Lecturing John Mbadi Like a child🤣 #trendingvideo #fyp #videoviral #viralvideo #kenyanpolitics', 'author': 'can_nyakundi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '055b6f0a', 'url': None, 'text': 'When John Mbadi started choking and sweating, MPs were heard shouting, "Kunywa maji, expert" 😅', 'author': 'plugtvkenya', 'platform': 'instagram', 'source_type': 'video'}], 'summary': "In a mocking and humorous tone (Sheng/English), users highlight Mbadi's heated clashes with Sifuna and Babu Owino, portraying him as losing his temper and being publicly embarrassed."}], 'supportive': [{'theme': 'Praised as an effective and communicative Treasury CS delivering real tax relief', 'quotes': [{'ref': '189d2a09', 'url': None, 'text': 'CS John Mbadi = The perfect Treasury CS ever 🇰🇪 He explains the Finance Bill in simple words we ALL understand. No complicated economics. Just straight talk: "16% VAT, 10% excise duty, 25% import duty, 2.5% import declaration fee, 2% railway levy" – that\'s what they add to your phone. Finally, a leader who respects Kenyan taxpayers. 👏 #CSJohnMbadi #FinanceBill #Kenya #kenyapolitics', 'author': 'michaelkamuren254', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '0906685e', 'url': None, 'text': '"If PAYE Cuts Are a Campaign Strategy, It\'s a Good Strategy," CS John Mbadi "If the PAYE cuts we are rolling out are a campaign strategy, so be it. It\'s a good strategy. At the end of the day, it\'s putting money in people\'s pockets."', 'author': 'money254hq', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '5e45892a', 'url': None, 'text': 'Listen to this carefully. CS Mbadi is the Best CS. #nairobitiktokers🇰🇪 #kenyan🌍equator #kenyantiktok🇰🇪', 'author': 'kenyanequator', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '5e4e5edc', 'url': None, 'text': 'Allow me to sincerely thank , the Cabinet Secretary for Finance, Hon. John Mbadi, for taking his time to be with us. Your presence, sir, is a great honor not only to me but to the entire Suba South community.', 'author': 'hon..lucy.manyala', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': 'Citizens and content creators praise Mbadi for PAYE reforms, clear communication on finance matters, and being accessible to ordinary Kenyans.'}, {'theme': 'Seen as a bold political actor willing to defend the government and challenge opponents', 'quotes': [{'ref': '1008b4fe', 'url': None, 'text': "CS Mbadi has just exposed a shocking plot: Uhuru Kenyatta allegedly had a plan to destabilize President Ruto's government within the first 3 months after the swearing-in. Yes, even before Ruto could fully settle. Now, the same networks are back – funding opposition groups like Linda Mwananchi, exploiting global fuel issues to create unnecessary chaos, and threatening our democratic process.", 'author': 'michaelkamuren254', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '65f53f5b', 'url': None, 'text': 'Cs Mbadi has refused to be toyed around by people who thinks the ODM party is a family property. He claims that not because Ruth Odinga is a sister to the Party leader he has a right to invite Edwin Sifuna back to the party.', 'author': 'brooklin042', 'platform': 'tiktok', 'source_type': 'video'}], 'summary': "Supporters frame Mbadi as courageous for exposing alleged destabilization plots and defending President Ruto's government against opposition."}]}, 'platformPulse': [{'tone': 'Energetic, satirical, and politically charged — mixing entertainment with civic commentary. Reactions range from mockery to genuine shock, with a strong Gen Z sensibility.', 'quotes': [{'ref': 'a49929b3', 'url': None, 'text': 'JEFF KOINANGE COINS A NEW THEME SONG FOR JOHN MBADI. MBADI BOY!!! WHATCHA GONNA DO? JEFF!!! 🤩🤩🤩', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'db34598d', 'url': None, 'text': 'Sifuna has successfully turned hon mbadi to a preacher 🇰🇪🤣 now preaching politics 😂⚡⚡🇰🇪🤣', 'author': 'johnny.drycon1', 'platform': 'tiktok', 'source_type': 'video'}], 'themes': ['Budget 2026 reactions and shock', 'Mbadi vs Sifuna political rivalry', "Mbadi's political ambitions and leadership credibility", 'IACC raid on Mbadi', 'Budget Day drama and parliamentary spectacle'], 'platform': 'tiktok', 'mention_count': 174, 'notable_voices': ['@genzdata7', '@dazavin_1st', '@db34598d (johnny.drycon1)', '@rtc_media', '@maxwellmax86', '@a49929b3 (_evansmwirigi)', '@tiktoktrends_tk1']}, {'tone': 'Mixed — mainstream media outlets post formal news updates and budget figures, while politicians and public figures post pointed challenges or institutional statements. Tone shifts between sober fiscal reporting and combative political exchanges.', 'quotes': [{'ref': '9c6064d6', 'url': None, 'text': 'I had these questions for the Treasury CS John Mbadi. We demand immediate answers.', 'author': 'he.babuowino', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'e0da2a0c', 'url': None, 'text': 'CS Mbadi to Winnie Odinga: You have no capacity to advise me on how to run the ministry of finance. Be elected first.', 'author': 'ntvkenya', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'f12c9ba7', 'url': None, 'text': 'CS Mbadi: The main feedback (from Kenyans) was that the govt should reduce the overall cost of living by lowering the tax burden on essential commodities, tame the wastage of public resources and decisively deal with corruption.', 'author': 'ntvkenya', 'platform': 'instagram', 'source_type': 'video'}], 'themes': ['Budget 2026/27 presentation and figures', 'Mbadi clashing with Winnie Odinga and ODM family', 'Public debt and budget deficit', 'Village elders stipend controversy', "Mbadi's pre-budget preparations"], 'platform': 'instagram', 'mention_count': 219, 'notable_voices': ['@he.babuowino', '@williamsamoeiruto', '@ntvkenya', '@hon.wetangula', '@kenyans.co.ke', '@ktnnews']}, {'tone': 'In-depth and analytical, with a strong focus on accountability and political drama. Long-form content dominates, covering parliamentary grillings, ODM internal politics, and economic policy debates. Tone is serious but occasionally sensationalist.', 'quotes': [{'ref': '74f4c86b', 'url': None, 'text': 'JKL | ODM KICKS OUT SIFUNA| CS JOHN MBADI | QUICKFIRE', 'author': 'kenyacitizentv', 'platform': 'youtube', 'source_type': 'video'}, {'ref': '8f3e78d2', 'url': None, 'text': 'Ndindi Nyoro Is Wrong About Safaricom Shares Sale | Treasury CS John Mbadi', 'author': 'ntvkenyaonline', 'platform': 'youtube', 'source_type': 'video'}, {'ref': '01f57abc', 'url': None, 'text': 'Treasury CS John Mbadi reveals: Opposition wanted riots, Finance Bill ruined their plan', 'author': 'HermanManyora', 'platform': 'youtube', 'source_type': 'video'}], 'themes': ['ODM internal conflict and Sifuna clash', 'Safaricom shares privatisation debate', "Kenya's economic health and IMF policy", 'Fuel security and global economic risks', "Mbadi's political declarations and identity"], 'platform': 'youtube', 'mention_count': 67, 'notable_voices': ['@kenyacitizentv', '@ntvkenyaonline', '@ktnnews_kenya', '@AzimioTVOfficial', '@HermanManyora']}, {'tone': "Professional and policy-focused, with a mix of institutional commentary, economic analysis, and occasional political criticism. Discourse is more measured than other platforms but covers Mbadi's fiscal credibility closely.", 'quotes': [{'ref': '5e8ddd6c', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7419994119890108416', 'text': "CS Mbadi responds to Ndindi Nyoro: You can't just come with street talk. When it comes to valuing shares, it's not like selling fish or 'waru'.", 'author': 'Business Daily Africa', 'platform': 'linkedin', 'source_type': 'post'}, {'ref': 'eaf542da', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7426522568465002496', 'text': 'Treasury CS, John Mbadi declares war with Edwin Sifuna: "Some people must leave ODM party for it to remain intact, there are so many who can fill ODM SG position. Get rid of this rogue SG. Mkinyamaza mtapata ODM imegawanywa mara tatu."', 'author': 'The Statesman Digital', 'platform': 'linkedin', 'source_type': 'post'}], 'themes': ['PAYE tax reform promises', 'Finance Bill 2026 analysis and tax amnesty', 'Zero-based budgeting transition', 'ODM political tensions', "Budget framing as 'Mwananchi Budget'"], 'platform': 'linkedin', 'mention_count': 154, 'notable_voices': ['@Money254', '@Amboko H. Julians', '@Dr Chris Kiptoo CBS', '@The Standard', '@Business Daily Africa']}], 'timeline': [{'date': '2026-02-02', 'event': 'CS Mbadi announces plans to increase the tax-free PAYE threshold from Ksh24,000 to Ksh30,000, stating the amendment has presidential approval and will be tabled before the 2026 Finance Bill.', 'quotes': [{'ref': '6b796192', 'url': None, 'text': 'Kenyans Earning Ksh30k & Below to Pay Zero PAYE This Year - CS Mbadi Treasury Cabinet Secretary John Mbadi has announced plans to increase the tax-free pay from the current Ksh24,000 to Ksh30,000.', 'author': 'money254hq', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'f613d20b', 'url': None, 'text': 'Tax Exempt if you earn less than 30,000', 'author': 'trendzmototv', 'platform': 'tiktok', 'source_type': 'video'}], 'mentions_that_day': 18}, {'date': '2026-03-02', 'event': 'CS Mbadi appears on JKL amid ODM party tensions, accusing Edwin Sifuna of misleading Nyanza supporters against the government and declaring himself the senior-most Luo leader in government.', 'quotes': [{'ref': 'b7c3fab0', 'url': None, 'text': 'Treasury CS John Mbadi accuses Edwin Sifuna of misleading Nyanza supporters to go against government', 'author': 'ntvkenyaonline', 'platform': 'youtube', 'source_type': 'video'}], 'mentions_that_day': 17}, {'date': '2026-05-02', 'event': 'CS Mbadi signals presidential ambitions, stating confidence in running for the presidency in 2027.', 'quotes': [{'ref': '8e60a9bb', 'url': None, 'text': 'Am confident enough to go for the presidency in 2027, that seat belong to no one and i have all the qualifications to be there- Hon. John Mbadi.', 'author': 'ajwang_junior', 'platform': 'tiktok', 'source_type': 'video'}], 'mentions_that_day': 15}, {'date': '2026-05-29', 'event': 'CS Mbadi gives a full media briefing on the Finance Bill 2026, clashes with Babu Owino, and makes pointed remarks to Winnie Odinga over ODM succession, sparking wide public debate.', 'quotes': [{'ref': 'fe79e53a', 'url': None, 'text': 'CS Mbadi to Winnie Odinga: Win an elective seat before lecturing me', 'author': 'ntvkenyaonline', 'platform': 'youtube', 'source_type': 'video'}, {'ref': 'c800f36a', 'url': None, 'text': 'SHUT UP !YOU CANT LECTURE ME ON ECONOMY!Angry CS Mbadi Explodes at Babu Owino', 'author': 'CMNKENYA', 'platform': 'youtube', 'source_type': 'video'}], 'mentions_that_day': 18}, {'date': '2026-06-02', 'event': 'A public row erupts between CS Mbadi and the Odinga family over ODM succession remarks.', 'quotes': [{'ref': '3d6f0b44', 'url': None, 'text': 'Row erupts between Treasury CS John Mbadi and Odinga family over ODM succession remarks', 'author': 'kenyacitizentv', 'platform': 'youtube', 'source_type': 'video'}, {'ref': '911b4112', 'url': None, 'text': 'John Mbadi and Gladys Wanga Kicks Odinga Family Out of ODM Ruto Happy', 'author': 'kenyalens', 'platform': 'youtube', 'source_type': 'video'}], 'mentions_that_day': 17}, {'date': '2026-06-11', 'event': "CS Mbadi presents the Ksh 4.8 trillion FY 2026/27 Budget Statement before Parliament, arriving under heavy security, switching his usual car to a 2016 Peugeot 508, choking during his speech as MPs chanted 'Expert', and facing heckling over a Ksh3.9 billion village elders stipend proposal.", 'quotes': [{'ref': 'ab6798fa', 'url': None, 'text': 'Highly trained hard faced SWAT commandos who can smell explosives 100km away were present keeping CS Mbadi safe, this are just ladies at work.', 'author': 'theofficial_deepstate', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '055b6f0a', 'url': None, 'text': 'When John Mbadi started choking and sweating, MPs were heard shouting, "Kunywa maji, expert" 😅', 'author': 'plugtvkenya', 'platform': 'instagram', 'source_type': 'video'}], 'mentions_that_day': 177}, {'date': '2026-06-15', 'event': "CS Mbadi appears before Parliament and Senate to explain how the government will plug the Ksh1.1 trillion deficit, faces tough questioning from senators, and President Ruto reveals he initially opposed Mbadi's appointment as Treasury CS.", 'quotes': [], 'mentions_that_day': 27}, {'date': '2026-06-18', 'event': 'CS Mbadi faces grilling in the Senate and calls for a moment of silence in honour of Raila Odinga ahead of the Budget 2026/27 reading.', 'quotes': [{'ref': 'b79e7e3d', 'url': None, 'text': 'Fireworks as Senators pin down Treasury CS John Mbadi during his Grilling in Senate.', 'author': 'KenyaDigitalNews', 'platform': 'youtube', 'source_type': 'video'}, {'ref': 'b98599bc', 'url': None, 'text': 'CS Mbadi calls for a moment of silence in honour of Raila Odinga ahead of Budget 2026/27 Reading', 'author': 'kenyacitizentv', 'platform': 'youtube', 'source_type': 'video'}], 'mentions_that_day': 35}, {'date': '2026-07-01', 'event': "CS Mbadi appears on Jeff Koinange Live (JKL), where Jeff Koinange coins the viral theme song 'Mbadi Boy! Whatcha Gonna Do?' and grills Mbadi on national debt, the State House budget, JKIA, the Nyota programme, and the Finance Bill 2026.", 'quotes': [{'ref': 'a49929b3', 'url': None, 'text': 'JEFF KOINANGE COINS A NEW THEME SONG FOR JOHN MBADI. MBADI BOY!!! WHATCHA GONNA DO? JEFF!!! 🤩🤩🤩', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'ff4d4395', 'url': None, 'text': "CS John Mbadi: No country reduces it's public debt, we can hit even 30trillion in the future 🙄🚨", 'author': 'newdawntv.ke', 'platform': 'tiktok', 'source_type': 'video'}], 'mentions_that_day': 28}, {'date': '2026-07-02', 'event': "Continued viral reaction to CS Mbadi's JKL appearance, with clips of him being 'cooked' by Jeff Koinange circulating widely, alongside remarks on JKIA's state and data ownership concerns.", 'quotes': [{'ref': '1ffd9d89', 'url': None, 'text': 'John Mbadi Cooked himself on JKL🤣🤣🤣🤣', 'author': 'naivasha_post', 'platform': 'tiktok', 'source_type': 'video'}], 'mentions_that_day': 23}], 'influencerStances': [{'handle': 'money254hq', 'quotes': [{'ref': '6b796192', 'url': None, 'text': 'Kenyans Earning Ksh30k & Below to Pay Zero PAYE This Year - CS Mbadi Treasury Cabinet Secretary John Mbadi has announced plans to increase the tax-free pay from the current Ksh24,000 to Ksh30,000.', 'author': 'money254hq', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '0906685e', 'url': None, 'text': "If the PAYE cuts we are rolling out are a campaign strategy, so be it. It's a good strategy. At the end of the day, it's putting money in people's pockets.", 'author': 'money254hq', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'neutral', 'account_type': 'Financial education and personal finance content account, covering economic policy and tax news relevant to Kenyan earners', 'what_they_say': "Reports on Mbadi's tax policy announcements, particularly PAYE reforms, presenting his statements factually without strong editorial endorsement or criticism. Covers both the policy details and Mbadi's own defence of the changes.", 'influence_score': 2623.0}, {'handle': 'ntvkenya', 'quotes': [{'ref': 'e0da2a0c', 'url': None, 'text': 'CS Mbadi to Winnie Odinga: You have no capacity to advise me on how to run the ministry of finance. Be elected first.', 'author': 'ntvkenya', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'c10e22a8', 'url': None, 'text': 'Mbadi: ODM does not belong to a family. I know Baba has been a solid leader. We must now manage the transition. Baba is no more. This is the post-Raila era.', 'author': 'ntvkenya', 'platform': 'instagram', 'source_type': 'video'}], 'stance': 'neutral', 'account_type': 'Mainstream broadcast news media account, reporting on government policy, politics, and budget matters', 'what_they_say': "Provides straight news coverage of Mbadi's statements and actions as Treasury CS, including budget proposals, fuel stock updates, political disputes with the Odinga family, and Finance Bill clarifications. Does not editorially endorse or attack Mbadi.", 'influence_score': 2331.3}, {'handle': '_evansmwirigi', 'quotes': [{'ref': '401cafdf', 'url': None, 'text': 'JEFF KOINANGE LIVE: WAZIRI JOHN MBADI "TRIES TO MAKE RUTO" LOOK BETTER THAN UHURU KENYATTA. ATI MT. KENYA IS CONTENT? UONGO! 😒', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '8f6f6ab0', 'url': None, 'text': 'JEFF KOINANGE LIVE: WAZIRI JOHN MBADI ANSWERS CONCERN ON DATA BELONGING TO KENYANS, AFTER AN OFFLOAD OF 15% SHARES BY KENYA ADMINISTRATION. MBADI IS RUDE!!!!', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'critical', 'account_type': 'Political commentary and reaction account, focused on highlighting media interviews and political debates, with an informal and opinionated tone', 'what_they_say': 'Shares clips of Mbadi being questioned or challenged on TV, framing him negatively — characterising him as rude, evasive, or trying to make the current administration look better than predecessors. Uses exclamatory language suggesting Mbadi is being exposed or embarrassed.', 'influence_score': 1590.0}, {'handle': 'michaelkamuren254', 'quotes': [{'ref': '1008b4fe', 'url': None, 'text': "CS Mbadi has just exposed a shocking plot: Uhuru Kenyatta allegedly had a plan to destabilize President Ruto's government within the first 3 months after the swearing-in.", 'author': 'michaelkamuren254', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '189d2a09', 'url': None, 'text': 'CS John Mbadi = The perfect Treasury CS ever 🇰🇪 He explains the Finance Bill in simple words we ALL understand. No complicated economics. Just straight talk.', 'author': 'michaelkamuren254', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'supportive', 'account_type': 'Pro-government political commentary account, amplifying government narratives and defending the current administration', 'what_they_say': 'Strongly supports Mbadi, amplifying his claims about alleged political destabilisation plots and praising his communication style on the Finance Bill. Frames Mbadi as a truth-teller exposing opposition bad faith and as an exemplary CS.', 'influence_score': 892.3}, {'handle': 'tiktoktrends_tk1', 'quotes': [{'ref': '4e7e6a9e', 'url': None, 'text': 'john mbadi trending statement #tiktokdrama #tiktokkenya #trendingtiktoks John mbadi #johnmbadi kenyan politics #foryou @oga obinna tiktok', 'author': 'tiktoktrends_tk1', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'neutral', 'account_type': 'Trending content aggregator account, riding viral topics and hashtags on TikTok', 'what_they_say': 'Posts about Mbadi purely as a trending topic without substantive analysis or a discernible stance, using general trending hashtags to drive engagement.', 'influence_score': 827.0}, {'handle': 'ajwang_junior', 'quotes': [{'ref': '8e60a9bb', 'url': None, 'text': 'Am confident enough to go for the presidency in 2027, that seat belong to no one and i have all the qualifications to be there- Hon. John Mbadi.', 'author': 'ajwang_junior', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '1cf146a0', 'url': None, 'text': 'Hon. John Mbadi miwaa pesa makata kochopo niteri ICC to wayie wabiro kowi- Ker Jacaranda Mjuku.', 'author': 'ajwang_junior', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'mixed', 'account_type': 'Luo-language political commentary account, covering ODM internal politics and community-level political discourse', 'what_they_say': "Posts primarily in Dholuo about Mbadi's political manoeuvres within ODM, including his clashes with the Odinga family and his presidential ambitions. Also quotes Mbadi's own statements. The framing is observational with some implicit criticism of his loyalty and ambitions.", 'influence_score': 592.8}, {'handle': 'rtc_media', 'quotes': [{'ref': '55341d5a', 'url': None, 'text': "In simple terms, this is a clip of CS Mbadi saying Raila would not stop him from vying in any elective position after the 2022 elections no matter what he would do. Mbadi declared war on Raila long time ago...what makes you think he means well for Raila's party ODM!", 'author': 'rtc_media', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'critical', 'account_type': 'Opposition-leaning political commentary account, critical of the current government and sympathetic to the Odinga camp', 'what_they_say': "Consistently attacks Mbadi's loyalty to Raila Odinga and his political credibility, framing him as a traitor to ODM and someone who declared war on Raila long ago. Also mocks his presidential ambitions and criticises his ethnic and political statements.", 'influence_score': 562.2}, {'handle': 'nextgen_254', 'quotes': [{'ref': 'a12a2d88', 'url': None, 'text': 'Jeff cooking CS Mbadi, Mbadi Boy ako ready Kuhepa😂🤣🔥', 'author': 'nextgen_254', 'platform': 'tiktok', 'source_type': 'video'}], 'stance': 'mixed', 'account_type': 'Entertainment and political reaction account with a light, humorous tone', 'what_they_say': 'Posts a brief humorous comment about Mbadi being grilled in an interview, suggesting he was flustered or ready to flee, without taking a clearly defined ideological stance.', 'influence_score': 531.9}], 'narrativeDeepDives': [{'label': 'Mbadi Grilled on TV', 'quotes': [{'ref': 'a49929b3', 'url': None, 'text': 'JEFF KOINANGE COINS A NEW THEME SONG FOR JOHN MBADI. MBADI BOY!!! WHATCHA GONNA DO? JEFF!!! 🤩🤩🤩', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '401cafdf', 'url': None, 'text': 'JEFF KOINANGE LIVE: WAZIRI JOHN MBADI "TRIES TO MAKE RUTO" LOOK BETTER THAN UHURU KENYATTA. ATI MT. KENYA IS CONTENT? UONGO! 😒', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '440e0f9c', 'url': None, 'text': "JEFF KOINANGE LIVE:WAZIRI JOHN MBADI 'SCHOOLED' ON HOW KENYA CAN WIDEN TAX BASE, GIVING FAR MORE REPRIEVE. JAMAA HASKII 😒", 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'cee7c4aa', 'url': None, 'text': 'JEFF KOINANGE PUTS WAZIRI JOHN MBADI ON THE SPOT IN REGARD TO FY 26/27. FINYA!!!!', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '86ebf364', 'url': None, 'text': 'JEFF KOINANGE, COOKING WAZIRI JOHN MBADI && JUNET MOHAMED IN ABSENTIA. WANATOA WAPI PESA', 'author': '_evansmwirigi', 'platform': 'tiktok', 'source_type': 'video'}], 'mention_count': 5, 'critic_framing': "No sources in the provided material present a defense of Mbadi or a critique of Koinange's interview style. The available content is uniformly critical of Mbadi's performance and celebratory of the grilling.", 'how_it_unfolded': "The story centers on a Jeff Koinange Live television appearance by Finance Minister John Mbadi, captured and shared across TikTok on July 1, 2026. The clips show Koinange subjecting Mbadi to pointed on-air questioning across several areas: government spending and where the money comes from ('WANATOA WAPI PESA'), taxation and how Kenya could widen its tax base, the FY 26/27 budget, and political comparisons between Ruto and former President Uhuru Kenyatta. A recurring theme across the clips is Mbadi appearing to struggle or deflect under questioning — being described as 'schooled' on tax policy and 'put on the spot' on the budget. One clip specifically highlights Mbadi allegedly trying to make Ruto look better than Uhuru Kenyatta by claiming Mt. Kenya is content, a claim the poster flatly rejects. The session was framed as combative enough that Koinange was described as 'cooking' both Mbadi and Junet Mohamed (the latter in absentia). The clips were packaged as entertainment as well as political commentary, with Koinange even being credited with coining a mocking theme song — 'Mbadi Boy!!!' — for the minister.", 'supporter_framing': "Supporters of the grilling — as visible through @_evansmwirigi's framing — celebrate Koinange as a tough interrogator holding a government minister to account. They portray Mbadi as evasive, uninformed on tax policy ('JAMAA HASKII'), and as a political spinner trying to rehabilitate Ruto's image at the expense of factual accuracy. The tone is gleeful and mocking, with exclamations like 'FINYA!!!!' (squeeze/press him) signaling approval of Koinange's aggressive questioning. The claim that Mt. Kenya is content under Ruto is dismissed as 'UONGO!' (lies), framing Mbadi as dishonest.", 'who_is_driving_it': ['@_evansmwirigi']}, {'label': 'Budget Allocation Proposals', 'quotes': [{'ref': '40cccb26', 'url': None, 'text': 'CS Mbadi proposes Sh784.5 billion allocation to the Education sector, including Sh56.3 billion for HELB and Sh30.9 billion for university scholarships. #Budget2026', 'author': 'ntvkenya', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'f4024a8a', 'url': None, 'text': 'CS John Mbadi : I am proposing 9.4 billion for settlement of the landless, 5 billion is for the landless in the Coast region.', 'author': 'k24tv', 'platform': 'instagram', 'source_type': 'video'}, {'ref': '2a8d1143', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7470840100260728833', 'text': 'Treasury CS John Mbadi proposes KSh 9.4 billion for the settlement of the homeless in the 2026/27 national budget', 'author': 'The Kenya Times', 'platform': 'linkedin', 'source_type': 'post'}], 'mention_count': 3, 'critic_framing': 'The provided sources do not contain any critical voices or oppositional framing of these budget proposals. No critics, counter-arguments, or skeptical commentary appear in the available material.', 'how_it_unfolded': "The story centers on CS John Mbadi's budget allocation proposals for the 2026/27 national budget, surfacing across TikTok, Instagram, and LinkedIn between June 11 and June 18, 2026. Two major spending proposals are reported: a KSh 784.5 billion allocation to the Education sector — broken down into KSh 56.3 billion for HELB and KSh 30.9 billion for university scholarships — and a KSh 9.4 billion allocation for settlement of the landless, with KSh 5 billion of that specifically earmarked for the landless in the Coast region. The NTV Kenya TikTok post on the education allocation drew by far the most engagement (over 405,000 interactions), suggesting it resonated strongly with the public. The K24TV Instagram post captured Mbadi's direct remarks on the landless settlements, while The Kenya Times LinkedIn post framed the same settlement proposal in terms of the homeless, slightly broadening the characterization.", 'supporter_framing': 'Supporters and media amplifying the proposals highlight the scale and specificity of the commitments — particularly the large education budget covering student loans (HELB) and university scholarships, as well as the targeted relief for landless communities, especially those in the Coast region. The framing treats these as concrete, socially meaningful investments in historically underserved populations.', 'who_is_driving_it': ['@ntvkenya', '@k24tv', '@The Kenya Times']}, {'label': 'Mbadi Budget Presentation', 'quotes': [{'ref': '1f3549ae', 'url': None, 'text': 'CS John Mbadi escorted by heavy security from the National Treasury to Parliament to present the Sh4.8 trillion 2026/27 budget.', 'author': 'theeastleighvoice', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'c5c5c959', 'url': None, 'text': 'CS John Mbadi exits the National Treasury with the iconic budget briefcase as he heads to Parliament to present the Sh4.8 trillion 2026/27 budget. He is accompanied by Treasury PS Chris Kiptoo and other Ministry officials.', 'author': 'theeastleighvoice', 'platform': 'instagram', 'source_type': 'video'}, {'ref': '5f31a088', 'url': None, 'text': 'CS Treasury John Mbadi reviews key budget documents en route to Parliament ahead of presenting the KSh 4.29 trillion FY 2026/27 Budget Statement.', 'author': 'plugtvkenya', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'b5a890d0', 'url': None, 'text': 'Safina Party leader Jimmy Wanjigi dares CS Treasury John Mbadi to explain to Kenyans how Ksh 4trillion borrowed by the government has been used.', 'author': 'pmtv_ke', 'platform': 'instagram', 'source_type': 'post'}], 'mention_count': 33, 'critic_framing': 'Critical voices were limited in this storyline but present on the margins. Safina Party leader Jimmy Wanjigi used the budget moment as a platform to challenge accountability, daring Mbadi to explain to Kenyans how Ksh 4 trillion borrowed by the government has been used. This framing cast the budget presentation less as a celebration and more as an occasion demanding fiscal transparency and answers on debt.', 'how_it_unfolded': "On June 11, 2026, Treasury CS John Mbadi's journey from the National Treasury to Parliament to present the Sh4.8 trillion 2026/27 budget was extensively documented across Kenyan social media. The story unfolded in stages: first, security was heightened around the National Treasury building itself; then Mbadi was photographed and filmed reviewing budget documents, taking photos outside with the iconic budget briefcase, and addressing journalists. He was accompanied by Treasury PS Chris Kiptoo and other Ministry officials. A heavy police and security escort then conveyed him to Parliament, where he was received by MPs including Kimani Kuria, Sam Atandi, and Deputy Speaker Gladys Boss. Upon arrival, MPs reportedly chanted 'Expert! Expert!' in reception. The budget figure cited across sources ranged between Ksh 4.29 trillion and Sh4.8 trillion depending on the outlet. A marginal critical thread appeared later, with Safina Party leader Jimmy Wanjigi challenging Mbadi to account for Ksh 4 trillion in government borrowing.", 'supporter_framing': "Supporters and mainstream media framed the event as a ceremonial and historic occasion, emphasizing Mbadi's preparedness — showing him reviewing documents en route to Parliament — and the significance of the Sh4.8 trillion budget. The chanting of 'Expert! Expert!' by MPs upon his arrival underscored a narrative of professional credibility and confidence in Mbadi's stewardship of the Treasury. The iconic budget briefcase, heavy security, and formal procession with PS Chris Kiptoo were all presented as markers of institutional gravitas.", 'who_is_driving_it': ['@plugtvkenya', '@citizen.digital', '@lightcasttvkenya', '@citizentvkenya', '@kenyacitizentv', '@kakajtv', '@KBCChannel1News', '@ktnnews_kenya', '@tv47_ke', '@tv47ke', '@theeastleighvoice', '@thestarkenya', '@btgnewske', '@pmtv_ke', '@ktnnews', '@mamboleoeast', '@mwatufm', '@EastleighVoice', '@KenyaDigitalNews', '@The Kenya Times']}, {'label': 'Mbadi Budget Leadership', 'quotes': [{'ref': 'f8ba9c14', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7427476506362269697', 'text': 'I never imagined he was a genius, but the way John Mbadi explains and handles things proves he truly deserves his position. Incredible leadership!', 'author': 'Tom Ogwe', 'platform': 'linkedin', 'source_type': 'post'}, {'ref': 'f8ba9c14', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7427476506362269697', 'text': 'What an honest brilliant man! Hon. John Mbadi is the best candidate to succeed H.E Ruto in 2032. The warm connection btwn him President is reassuring', 'author': 'Tom Ogwe', 'platform': 'linkedin', 'source_type': 'post'}, {'ref': 'f8ba9c14', 'url': 'https://www.linkedin.com/feed/update/urn:li:activity:7427476506362269697', 'text': 'Mbadi seems to be understanding his job,he equally simplifies issues for the ordinary citizen', 'author': 'Tom Ogwe', 'platform': 'linkedin', 'source_type': 'post'}, {'ref': '5b21eb40', 'url': None, 'text': 'he expressed confidence that the Authority will collect more revenue in the coming financial year without raising tax rates, adding that the Finance Bill focuses more on reforms and tax administration than new taxes.', 'author': 'theeastleighvoice', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'f48d8840', 'url': None, 'text': 'I have deliberately chosen not to introduce new taxes or increase tax rates that would further overburden the hard-working Kenyans and their families. Instead, the measures are focused on reforms that improve efficiency in tax collection, create fairness in the tax system and broaden the revenue base without burdening the Mwananchi.', 'author': '    Macharia Kamau\n', 'platform': 'www.tnx.africa', 'source_type': 'article'}], 'mention_count': 15, 'critic_framing': "The sources contain no direct critics attacking Mbadi personally on his budget leadership. However, the TNX Africa article provides implicit institutional criticism: analysts are quoted warning that the budget's Sh4.82 trillion spending is 'too high' and 'not translating to better lives for Kenyans,' that KRA revenue targets are 'too ambitious,' and that high domestic borrowing risks crowding out private lending. The article also notes Mbadi 'appeared to fall short' on delivering the cost-of-living relief he had signalled during public participation forums, and that calls for fiscal discipline 'have not been reflected in the budget-making process.' The Finance Bill's VAT on digital transactions and reclassification of essential goods are described as 'not sitting well with many Kenyans,' suggesting a gap between Mbadi's plain-speaking positioning and actual policy outcomes.", 'how_it_unfolded': "The storyline around Mbadi's budget leadership emerges primarily from a LinkedIn post aggregating public commentary, and from institutional coverage of his June 2026 budget presentation. On the commentary side, a Tom Ogwe LinkedIn post frames Mbadi as having 'effectively dismantled narrative warfare' against the government and credits him with calling the bluff of international financial institutions. Embedded within that post are multiple commenter reactions praising his intellect, communication style, and future political potential. On the institutional side, a Theeastleighvoice Instagram post reports Mbadi's pre-budget remarks expressing confidence in revenue collection without new tax hikes, framing the Finance Bill as focused on 'reforms and tax administration rather than new taxes.' A TNX Africa article covers his actual budget speech in Parliament, where Mbadi himself states he 'deliberately chose not to introduce new taxes,' and frames the Finance Bill as broadening the tax base rather than burdening existing taxpayers. The praise narrative is thus driven by his plain-speaking public positioning on taxes, amplified by supportive online commenters who project presidential ambitions onto him.", 'supporter_framing': "Supporters frame Mbadi as a uniquely capable and intellectually honest leader who simplifies complex fiscal matters for ordinary citizens. Commenters in the Tom Ogwe LinkedIn post describe him as a 'genius,' an 'honest brilliant man,' and argue he 'simplifies issues for the ordinary citizen.' Several explicitly call for him to be retained as Finance CS beyond 2027, and at least two commenters name him as the best candidate for the presidency in 2032. His stated commitment to not introducing new taxes while broadening the tax base is cited as evidence of practical, citizen-friendly leadership. His chairing of the National Infrastructure Fund Governing Council is presented in institutional posts as a sign of his centrality to Kenya's fiscal architecture.", 'who_is_driving_it': ['@Tom Ogwe', '@theeastleighvoice', '@Construction Today🇰🇪', '@Kenyans.co.ke']}, {'label': 'Village Elders Stipend Controversy', 'quotes': [{'ref': '03e04b1b', 'url': None, 'text': 'I have proposed Ksh3.9B for stipend to village elders for the first time, to enhance local administrative capacity and to appreciate and recognise the role played by village elders in helping to address security and other societal challenges.', 'author': 'k24tv', 'platform': 'instagram', 'source_type': 'video'}, {'ref': '1e519732', 'url': None, 'text': 'MPs reportedly heckled John Mbadi after a proposal to allocate KSh3.9 billion as stipends for village elders. 👀 The proposal has sparked debate over government spending priorities and the role of village elders in community administration.', 'author': 'rianboss254', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': '0a6e87ad', 'url': None, 'text': 'I have proposed Ksh3.9 billion for stipend to village elders - Treasury CS John Mbadi on the 2026/27 budget.', 'author': 'nairobi.leo', 'platform': 'instagram', 'source_type': 'video'}, {'ref': 'bcd71f1f', 'url': None, 'text': 'MPs heckle CS Mbadi after proposing Ksh3.9 billion as stipend for village elders', 'author': 'kenyanske', 'platform': 'tiktok', 'source_type': 'video'}, {'ref': 'e30ed614', 'url': None, 'text': 'MPs heckle CS John Mbadi after he proposed Ksh3.9 billion stipend to village elders', 'author': 'vantagekegram', 'platform': 'instagram', 'source_type': 'video'}], 'mention_count': 8, 'critic_framing': 'Critics, as represented by the heckling MPs, appear to view the Ksh3.9 billion allocation as a misplacement of government spending priorities, though specific alternative uses are not detailed in the sources. The heckling itself signals skepticism or disapproval of directing such a large sum toward village elder stipends.', 'how_it_unfolded': 'On June 11, 2026, Treasury CS John Mbadi presented the 2026/27 budget and announced a proposal to allocate Ksh3.9 billion as stipends for village elders — described as a first-time allocation. The announcement was met with heckling from MPs in the chamber, which was captured and widely shared across TikTok, Instagram, and LinkedIn. Multiple media outlets and accounts amplified the moment, framing it as a flashpoint in the broader debate over government spending priorities. Mbadi defended the proposal by citing the role village elders play in local administration, security, and addressing societal challenges. The story generated significant engagement, particularly on TikTok and Instagram, with the heckling incident itself serving as the hook that drove virality.', 'supporter_framing': "Supporters of the proposal, echoing Mbadi's own framing, argue the stipend is meant to enhance local administrative capacity and formally recognize the contribution of village elders in addressing security and other societal challenges — positioning it as a worthwhile investment in grassroots governance.", 'who_is_driving_it': ['@kenyanske', '@kenyans.co.ke', '@rianboss254', '@k24tv', '@nairobi.leo', '@vantagekegram', '@Vantage Ke', '@Martin Wachira']}], 'topComments': [], 'recency': {'last7Days': 63, 'last30Days': 349}},
}


def _run_report_job(job_id: str, name: str) -> None:
    db = SessionLocal()
    try:
        politician = _ensure_politician(db, name)
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=210)
        # Surface progress to the polling frontend: a full first-time run is
        # minutes long (fan-out scraping, then LLM analysis), and a silent
        # spinner reads as broken.
        _jobs[job_id]["stage"] = f"Scanning social platforms and news for “{name}”…"
        from engine.pipeline import run_analysis, run_ingestion

        ingestion_run = run_ingestion(db, politician, window_start, window_end, credit_budget=300.0)
        stats = (ingestion_run.stats or {}) if ingestion_run else {}
        found = stats.get("mentions_total")
        _jobs[job_id]["stage"] = (
            f"Analyzing {found} collected mentions (sentiment, narratives, voices, network)…"
            if found
            else "Analyzing collected mentions (sentiment, narratives, voices, network)…"
        )
        report = run_analysis(db, politician, "live-demo", window_start, window_end, ingestion_run=ingestion_run)
        _jobs[job_id] = {
            "status": "done",
            "ok": True,
            "report": _build_frontend_payload(politician, report),
            "created_at": time.time(),
        }
    except Exception:  # noqa: BLE001 - demo endpoint must never hard-fail the UI
        # Log the full traceback server-side; never leak internals to clients.
        traceback.print_exc()
        _jobs[job_id] = {
            "status": "done",
            "ok": False,
            "error": "report generation failed — see server logs",
            "created_at": time.time(),
        }
    finally:
        db.close()


@app.post("/api/report")
def generate_report(req: ReportRequest, request: Request, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    _check_rate_limit(request.client.host if request.client else "unknown")
    _evict_stale_jobs()

    name = req.name.strip()
    job_id = uuid.uuid4().hex
    precached = _PRECACHED_REPORTS.get(name.lower())
    if precached is not None:
        _jobs[job_id] = {"status": "done", "ok": True, "report": precached, "created_at": time.time()}
        return {"ok": True, "job_id": job_id}

    _jobs[job_id] = {"status": "running", "created_at": time.time()}
    thread = threading.Thread(target=_run_report_job, args=(job_id, name), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/report/{job_id}")
def get_report(job_id: str, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job


def _latest_report_core(db, name: str) -> dict | None:
    from engine.db.models import IntelligenceReport

    politician = db.query(Politician).filter_by(name=name.strip()).first()
    if not politician:
        return None
    report = (
        db.query(IntelligenceReport)
        .filter_by(politician_id=politician.id)
        .order_by(IntelligenceReport.generated_at.desc())
        .first()
    )
    if not report:
        return None
    p = report.payload or {}
    return {
        "name": politician.name,
        "generated_at": str(report.generated_at),
        "sentiment": p.get("sentiment_breakdown", {}),
        "volume": {
            "total": (p.get("volume_trends") or {}).get("total_mentions"),
            "by_platform": (p.get("volume_trends") or {}).get("by_platform", {}),
        },
        "narratives": [
            {"label": n["label"], "strength": n["strength_score"], "mentions": n["mention_count"]}
            for n in (p.get("narrative_breakdown") or [])[:8]
        ],
        "top_influencers": [i.get("author_handle") for i in (p.get("influence_summary") or [])[:10]],
        "executive_summary": p.get("executive_summary", ""),
    }


class SimulateRequest(BaseModel):
    name: str
    draft_text: str


@app.post("/api/simulate")
def simulate(req: SimulateRequest, request: Request, x_api_key: str | None = Header(default=None)):
    """Predict reception of a draft message from documented past reactions."""
    _require_api_key(x_api_key)
    _check_rate_limit(request.client.host if request.client else "unknown")
    from engine.intelligence.simulator import simulate_message

    db = SessionLocal()
    try:
        politician = db.query(Politician).filter_by(name=req.name.strip()).first()
        if not politician:
            raise HTTPException(status_code=404, detail="unknown politician — generate a report first")
        result = simulate_message(db, politician, req.draft_text.strip())
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="simulation failed — see server logs")
    finally:
        db.close()
    return {"ok": True, "simulation": result}


@app.get("/api/compare")
def compare(a: str, b: str, x_api_key: str | None = Header(default=None)):
    """Side-by-side comparison of the latest stored reports for two names —
    rivals on one screen. Reads stored data only; never triggers pipelines."""
    _require_api_key(x_api_key)
    db = SessionLocal()
    try:
        left = _latest_report_core(db, a)
        right = _latest_report_core(db, b)
    finally:
        db.close()
    if not left or not right:
        missing = a if not left else b
        raise HTTPException(status_code=404, detail=f"no stored report for '{missing}' — generate one first")
    shared = sorted(
        {n["label"] for n in left["narratives"]} & {n["label"] for n in right["narratives"]}
    )
    shared_influencers = sorted(set(left["top_influencers"]) & set(right["top_influencers"]))
    return {"ok": True, "a": left, "b": right,
            "shared_narratives": shared, "shared_influencers": shared_influencers}


@app.get("/api/alerts")
def get_alerts(name: str, limit: int = 30, x_api_key: str | None = Header(default=None)):
    """Early-warning feed for a tracked politician, newest first."""
    _require_api_key(x_api_key)
    from engine.db.models import Alert

    db = SessionLocal()
    try:
        politician = db.query(Politician).filter_by(name=name.strip()).first()
        if not politician:
            return {"ok": True, "alerts": []}
        rows = (
            db.query(Alert)
            .filter(Alert.politician_id == politician.id)
            .order_by(Alert.created_at.desc())
            .limit(min(limit, 100))
            .all()
        )
        return {
            "ok": True,
            "alerts": [
                {
                    "severity": a.severity,
                    "kind": a.kind,
                    "headline": a.headline,
                    "detail": a.detail,
                    "evidence": a.evidence or [],
                    "created_at": str(a.created_at),
                }
                for a in rows
            ],
        }
    finally:
        db.close()


@app.get("/api/livefeed")
def livefeed(name: str, limit: int = 40, x_api_key: str | None = Header(default=None)):
    """War-room feed: recent mentions, hourly volume curve, live narrative
    strengths. Cheap DB reads only — safe to poll every ~15s."""
    _require_api_key(x_api_key)
    from engine.db.models import Narrative, NarrativeMetric

    db = SessionLocal()
    try:
        politician = db.query(Politician).filter_by(name=name.strip()).first()
        if not politician:
            raise HTTPException(status_code=404, detail="unknown politician")
        rows = (
            db.query(RawMention, MentionSentiment)
            .outerjoin(MentionSentiment, MentionSentiment.mention_id == RawMention.id)
            .filter(RawMention.politician_id == politician.id, RawMention.is_spam == 0)
            .order_by(RawMention.posted_at.desc())
            .limit(min(limit, 100))
            .all()
        )
        ticker = [
            {
                "text": (m.text or "")[:180],
                "author": m.author_handle,
                "platform": m.platform,
                "source_type": m.source_type,
                "sentiment": s.sentiment if s else None,
                "posted_at": str(m.posted_at),
                "url": m.source_url,
            }
            for m, s in rows
        ]
        cutoff = datetime.utcnow() - timedelta(hours=48)
        recent = (
            db.query(RawMention.posted_at)
            .filter(RawMention.politician_id == politician.id, RawMention.posted_at >= cutoff)
            .all()
        )
        hourly: dict[str, int] = {}
        for (ts,) in recent:
            key = ts.strftime("%Y-%m-%dT%H:00")
            hourly[key] = hourly.get(key, 0) + 1
        storms = [
            {"label": n.label, "strength": metric.strength_score, "growth": metric.growth_rate}
            for n, metric in (
                db.query(Narrative, NarrativeMetric)
                .join(NarrativeMetric, NarrativeMetric.narrative_id == Narrative.id)
                .filter(Narrative.politician_id == politician.id)
                .order_by(NarrativeMetric.strength_score.desc())
                .limit(10)
                .all()
            )
        ]
        return {"ok": True, "ticker": ticker, "hourly_volume": dict(sorted(hourly.items())), "storms": storms}
    finally:
        db.close()


_baraza_cache: dict = {"at": 0.0, "data": None}


@app.get("/api/baraza")
def baraza():
    """PUBLIC weekly leaderboard — intentionally unauthenticated and cached."""
    now = time.time()
    if _baraza_cache["data"] is None or now - _baraza_cache["at"] > 3600:
        from engine.reports.baraza import build_baraza_index

        db = SessionLocal()
        try:
            _baraza_cache["data"] = build_baraza_index(db)
            _baraza_cache["at"] = now
        finally:
            db.close()
    return {"ok": True, "index": _baraza_cache["data"]}


@app.get("/api/health")
def health():
    """Liveness + data-supply state: the operator's one-glance view of
    whether the scraping supply chain is healthy before a demo."""
    supply: dict = {}
    if settings.socialcrawl_api_key:
        try:
            from engine.ingestion.socialcrawl_connector import SocialCrawlConnector

            supply["socialcrawl_credit_balance"] = SocialCrawlConnector().check_balance()
        except Exception:
            supply["socialcrawl_credit_balance"] = None
    db = SessionLocal()
    try:
        from engine.db.models import IngestionRun

        last_run = db.query(IngestionRun).order_by(IngestionRun.started_at.desc()).first()
        if last_run:
            supply["last_ingestion"] = {
                "status": last_run.status,
                "started_at": str(last_run.started_at),
                "mentions_total": (last_run.stats or {}).get("mentions_total"),
                "source_health": (last_run.stats or {}).get("source_health"),
            }
    except Exception:
        pass
    finally:
        db.close()
    return {"ok": True, "data_supply": supply}

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
            for politician in db.query(Politician).all():
                try:
                    window_end = datetime.utcnow()
                    window_start = window_end - timedelta(days=7)
                    run_ingestion(db, politician, window_start, window_end,
                                  credit_budget=settings.scheduled_credit_budget)
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
    "mbadi": {"name": "Mbadi", "title": "", "window": "2025-12-04 – 2026-07-02", "generated": "2026-07-02", "summary": "Across 544 mentions tracked between December 2025 and July 2026, Treasury CS John Mbadi's online sentiment is majority neutral (52.8%), with positive sentiment (27.0%) outpacing negative (20.2%), suggesting a public that is watchful but not yet decisively hostile. The dominant narrative is the high-profile Jeff Koinange TV grilling (strength score: 211.97), closely followed by the 2026/27 Budget Allocation Proposals and the Finance Bill 2026 debate — positioning fiscal policy communication as the central battleground for Mbadi's public standing. Conversation is concentrated on Instagram (200 mentions) and TikTok (118 mentions), indicating that younger, visually-driven audiences are the primary amplifiers, with LinkedIn (150) adding a professional layer that could be leveraged for credibility-building. Influence is heavily shaped by media handles — NTV Kenya and _evansmwirigi carry significant negative sentiment contributions (-8.0 and -17.0 respectively), meaning broadcast and commentary voices are actively pulling perception downward. The single most important takeaway for Mbadi's team is that his fiscal narrative is gaining traction but remains vulnerable to negative framing by influential media personalities; a proactive, platform-specific communications strategy — particularly on TikTok and Instagram — is urgently needed to convert neutral audiences into supporters before the Finance Bill 2026 debate hardens public opinion against him.", "sentiment": {"negative": 20.2, "neutral": 52.8, "positive": 27.0, "totalAnalyzed": 544}, "volume": {"total": 544, "platforms": 15, "last72h": 41, "byPlatform": [{"platform": "instagram", "count": 200, "share": 36.8}, {"platform": "linkedin", "count": 150, "share": 27.6}, {"platform": "tiktok", "count": 118, "share": 21.7}, {"platform": "youtube", "count": 63, "share": 11.6}, {"platform": "news", "count": 2, "share": 0.4}, {"platform": "www.tnx.africa", "count": 2, "share": 0.4}, {"platform": "reddit", "count": 1, "share": 0.2}, {"platform": "softpower.ug", "count": 1, "share": 0.2}, {"platform": "hivileo.co.ke", "count": 1, "share": 0.2}, {"platform": "newshub.co.ke", "count": 1, "share": 0.2}, {"platform": "www.msymi.com", "count": 1, "share": 0.2}, {"platform": "afrinewske.com", "count": 1, "share": 0.2}, {"platform": "nairobiwire.com", "count": 1, "share": 0.2}, {"platform": "blacknewsdaily.com", "count": 1, "share": 0.2}, {"platform": "elefundcapital.com", "count": 1, "share": 0.2}], "engagement": {"youtubeViews": 0, "linkedinLikes": 0, "linkedinShares": 0, "linkedinComments": 0}, "topMentions": [{"platform": "tiktok", "metric": "532505 views", "sentiment": "neutral", "headline": "Treasury CS John Mbadi to table a Ksh. 4.8 trillion budget statement before Parliament", "url": "https://www.tiktok.com/@citizen.digital/video/7650093524616088853"}, {"platform": "tiktok", "metric": "388573 views", "sentiment": "neutral", "headline": "CS Mbadi proposes Sh784.5 billion allocation to the Education sector, including Sh56.3 billion for HELB and Sh30.9 billion for university sc", "url": "https://www.tiktok.com/@ntvkenya/video/7650146636039015698"}, {"platform": "tiktok", "metric": "275634 views", "sentiment": "negative", "headline": "The truth must be told, even when it’s uncomfortable. CS Mbadi has just exposed a shocking plot: Uhuru Kenyatta allegedly had a plan to dest", "url": "https://www.tiktok.com/@michaelkamuren254/video/7643036377247993109"}, {"platform": "tiktok", "metric": "236496 views", "sentiment": "negative", "headline": "Highly trained hard faced SWAT commandos who can smell explosives 100km away were present keeping CS Mbadi safe, this are just ladies at wor", "url": "https://www.tiktok.com/@theofficial_deepstate/video/7650205137528556820"}, {"platform": "tiktok", "metric": "220434 views", "sentiment": "neutral", "headline": "Finance Bill 2026: We are not introducing any extra charges on MPesa - Treasury CS John Mbadi.", "url": "https://www.tiktok.com/@nairobileo.co.ke/video/7644128165027171604"}, {"platform": "tiktok", "metric": "189219 views", "sentiment": "negative", "headline": "Mbadi versus the Odingas: Fresh political storm brewing after Mbadi launched an unusual attack against some members of the Odinga family ove", "url": "https://www.tiktok.com/@ntvkenya/video/7640439284318670098"}, {"platform": "instagram", "metric": "170773 views", "sentiment": "neutral", "headline": "Treasury CS John Mbadi: \"We Are Not Introducing Any Extra Charges That Are Going To Affect Money Transfers Through M-Pesa.\" 🎥 @nairobi.leo", "url": "https://www.instagram.com/reel/DYzcVHXoNPT/"}, {"platform": "tiktok", "metric": "154019 views", "sentiment": "negative", "headline": "Babu Owino grills Treasury CS John Mbadi. Says he demands immediate answers.", "url": "https://www.tiktok.com/@lightcasttvkenya/video/7645016748210621714"}, {"platform": "tiktok", "metric": "163954 views", "sentiment": "neutral", "headline": "CS Mbadi to hold a meeting today at 4pm with the MATATU operators to see how government can cushion Kenyans on the high cost of fuel. #ruto ", "url": "https://www.tiktok.com/@.birgen/video/7641168601549917458"}, {"platform": "tiktok", "metric": "114475 views", "sentiment": "positive", "headline": "JEFF KOINANGE COINS A NEW THEME SONG FOR JOHN MBADI. MBADI BOY!!! WHATCHA GONNA DO? JEFF!!! 🤩🤩🤩", "url": "https://www.tiktok.com/@_evansmwirigi/video/7657633129502362888"}]}, "influence": [{"rank": 1, "score": 2431.6, "who": "money254hq", "platform": "", "note": "1 mention(s)", "sentiment": 2.0}, {"rank": 2, "score": 2318.4, "who": "ntvkenya", "platform": "", "note": "21 mention(s)", "sentiment": -8.0}, {"rank": 3, "score": 1584.1, "who": "_evansmwirigi", "platform": "", "note": "8 mention(s)", "sentiment": -17.0}, {"rank": 4, "score": 859.5, "who": "michaelkamuren254", "platform": "", "note": "1 mention(s)", "sentiment": -5.0}, {"rank": 5, "score": 827.0, "who": "tiktoktrends_tk1", "platform": "", "note": "1 mention(s)", "sentiment": 0.0}, {"rank": 6, "score": 529.0, "who": "rtc_media", "platform": "", "note": "4 mention(s)", "sentiment": -15.0}, {"rank": 7, "score": 332.5, "who": "nairobileo.co.ke", "platform": "", "note": "2 mention(s)", "sentiment": 0.0}, {"rank": 8, "score": 250.0, "who": "ajwang_junior", "platform": "", "note": "3 mention(s)", "sentiment": -5.0}, {"rank": 9, "score": 241.7, "who": "tonywakaromo1", "platform": "", "note": "1 mention(s)", "sentiment": -4.0}, {"rank": 10, "score": 179.0, "who": ".birgen", "platform": "", "note": "1 mention(s)", "sentiment": 0.0}], "network": {"nodes": [{"id": "subject", "label": "Mbadi", "group": "core", "value": 30}, {"id": "William_Ruto", "label": "William Ruto (Government of Kenya)", "group": "politician", "value": 62}, {"id": "Raila_Odinga", "label": "Raila Odinga (Orange Democratic Movement)", "group": "politician", "value": 50}, {"id": "Winnie_Odinga", "label": "Winnie Odinga (Orange Democratic Movement)", "group": "politician", "value": 22}, {"id": "Edwin_Sifuna", "label": "Edwin Sifuna (Orange Democratic Movement (ODM))", "group": "politician", "value": 18}, {"id": "Ndindi_Nyoro", "label": "Ndindi Nyoro (United Democratic Alliance)", "group": "politician", "value": 18}, {"id": "Sola_Yomi-Ajayi", "label": "Sola Yomi-Ajayi (Stragofin Advisory LLC)", "group": "person", "value": 16}, {"id": "Sanyade_Okoli", "label": "Sanyade Okoli (Office of the President of Nigeria)", "group": "person", "value": 14}, {"id": "Nat_Adams", "label": "Nat Adams (Newmont Corporation)", "group": "person", "value": 14}, {"id": "Ebru_Pakcan", "label": "Ebru Pakcan (Citigroup)", "group": "person", "value": 14}, {"id": "Wole_Famurewa", "label": "Wole Famurewa (U.S. Africa Exchange)", "group": "person", "value": 14}, {"id": "Dahlia_Khalifa", "label": "Dahlia Khalifa (International Finance Corporation (IFC))", "group": "person", "value": 14}, {"id": "Uhuru_Kenyatta", "label": "Uhuru Kenyatta (Jubilee Party)", "group": "politician", "value": 12}, {"id": "Haytham_El_Maayergi", "label": "Haytham El Maayergi (Afreximbank)", "group": "person", "value": 10}, {"id": "Ruth_Odinga", "label": "Ruth Odinga (ODM)", "group": "politician", "value": 10}], "edges": [{"from": "subject", "to": "William_Ruto", "label": "co-mentioned"}, {"from": "subject", "to": "Raila_Odinga", "label": "co-mentioned"}, {"from": "subject", "to": "Winnie_Odinga", "label": "co-mentioned"}, {"from": "subject", "to": "Edwin_Sifuna", "label": "co-mentioned"}, {"from": "subject", "to": "Ndindi_Nyoro", "label": "co-mentioned"}, {"from": "subject", "to": "Sola_Yomi-Ajayi", "label": "co-mentioned"}, {"from": "subject", "to": "Sanyade_Okoli", "label": "co-mentioned"}, {"from": "subject", "to": "Nat_Adams", "label": "co-mentioned"}, {"from": "subject", "to": "Ebru_Pakcan", "label": "co-mentioned"}, {"from": "subject", "to": "Wole_Famurewa", "label": "co-mentioned"}, {"from": "subject", "to": "Dahlia_Khalifa", "label": "co-mentioned"}, {"from": "subject", "to": "Uhuru_Kenyatta", "label": "co-mentioned"}, {"from": "subject", "to": "Haytham_El_Maayergi", "label": "co-mentioned"}, {"from": "subject", "to": "Ruth_Odinga", "label": "co-mentioned"}, {"from": "Sanyade_Okoli", "to": "Nat_Adams", "label": "appears together"}, {"from": "Sola_Yomi-Ajayi", "to": "Wole_Famurewa", "label": "appears together"}, {"from": "Sola_Yomi-Ajayi", "to": "Dahlia_Khalifa", "label": "appears together"}, {"from": "Sola_Yomi-Ajayi", "to": "Nat_Adams", "label": "appears together"}, {"from": "Wole_Famurewa", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Sanyade_Okoli", "to": "Sola_Yomi-Ajayi", "label": "appears together"}, {"from": "Sanyade_Okoli", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Sola_Yomi-Ajayi", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Dahlia_Khalifa", "to": "Wole_Famurewa", "label": "appears together"}, {"from": "Sanyade_Okoli", "to": "Wole_Famurewa", "label": "appears together"}, {"from": "Dahlia_Khalifa", "to": "Nat_Adams", "label": "appears together"}, {"from": "Dahlia_Khalifa", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Nat_Adams", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Sanyade_Okoli", "to": "Dahlia_Khalifa", "label": "appears together"}, {"from": "Nat_Adams", "to": "Wole_Famurewa", "label": "appears together"}, {"from": "Haytham_El_Maayergi", "to": "Ebru_Pakcan", "label": "appears together"}, {"from": "Uhuru_Kenyatta", "to": "William_Ruto", "label": "appears together"}, {"from": "Haytham_El_Maayergi", "to": "Nat_Adams", "label": "appears together"}, {"from": "Dahlia_Khalifa", "to": "Haytham_El_Maayergi", "label": "appears together"}, {"from": "Haytham_El_Maayergi", "to": "Wole_Famurewa", "label": "appears together"}, {"from": "Sanyade_Okoli", "to": "Haytham_El_Maayergi", "label": "appears together"}, {"from": "Sola_Yomi-Ajayi", "to": "Haytham_El_Maayergi", "label": "appears together"}, {"from": "Raila_Odinga", "to": "Ruth_Odinga", "label": "appears together"}, {"from": "Raila_Odinga", "to": "William_Ruto", "label": "appears together"}], "legend": [{"group": "core", "label": "Subject", "color": "#7dd3fc"}, {"group": "politician", "label": "Politician", "color": "#fbbf24"}, {"group": "person", "label": "Public figure", "color": "#c4b5fd"}, {"group": "influencer", "label": "Influencer (1k+ followers)", "color": "#86efac"}]}, "narratives": [{"label": "Mbadi grilled on TV", "strength": 211.97, "growth": 0.0, "mentions": 8, "description": "Jeff Koinange subjects Treasury Cabinet Secretary John Mbadi to tough on-air questioning over budget matters, government finances, and political comparisons."}, {"label": "Budget Allocation Proposals", "strength": 171.73, "growth": 1.0, "mentions": 3, "description": "Treasury CS John Mbadi has proposed major budget allocations for the 2026/27 national budget, including KSh 9.4 billion for landless settlement and KSh 784.5 billion for education."}, {"label": "Finance Bill 2026 Debate", "strength": 86.72, "growth": 0.5, "mentions": 5, "description": "Treasury CS John Mbadi is publicly explaining and defending the contents of the Finance Bill 2026, including clarifications on dropped taxes and no new MPesa charges, while facing criticism over his communication approach."}, {"label": "Mbadi Leadership Scrutiny", "strength": 78.83, "growth": 0.5, "mentions": 5, "description": "Social media users are divided over Treasury CS John Mbadi's leadership credibility, with some praising his accountability and directness while others question his competence and accuse him of illegal conduct."}, {"label": "Mbadi Budget Presentation", "strength": 49.05, "growth": 0.0, "mentions": 28, "description": "Treasury CS John Mbadi made high-profile preparations and arrived at Parliament under heavy security to present the Sh4.8 trillion 2026/27 national budget."}, {"label": "Infrastructure Fund Explanation", "strength": 42.66, "growth": 0.5, "mentions": 5, "description": "CS John Mbadi outlined the National Infrastructure Fund as a vehicle to finance major infrastructure projects through private capital from pension funds, banks, and privatisation proceeds, reducing dependence on taxation and public borrowing."}, {"label": "ODM Internal Power Struggle", "strength": 29.16, "growth": 0.2, "mentions": 11, "description": "Treasury CS John Mbadi publicly clashes with ODM Secretary General Edwin Sifuna and Ruth Odinga, rejecting family-based authority within the party and calling for unified, non-dynastic leadership of ODM."}, {"label": "Mbadi Defends Economy", "strength": 27.14, "growth": 0.33, "mentions": 7, "description": "CS John Mbadi makes trending controversial statements defending the government's economic performance, including public debt levels, agricultural growth, State House renovations, and GDP figures."}, {"label": "Mbadi vs Odinga Feud", "strength": 21.82, "growth": 0.0, "mentions": 6, "description": "Treasury CS John Mbadi publicly clashed with Winnie Odinga, dismissing her political criticism and telling her to first win an elective seat before offering him advice, while also declaring his own presidential ambitions."}, {"label": "Mbadi Budget Scrutiny", "strength": 19.19, "growth": 0.33, "mentions": 7, "description": "Social media users are discussing CS John Mbadi in the context of the budget and Finance Bill 2026, with reactions ranging from anticipation of a public forum to criticism over alleged dishonesty about financial matters."}, {"label": "Village Elders Stipend", "strength": 17.28, "growth": 0.5, "mentions": 5, "description": "Treasury CS John Mbadi's proposal of Ksh3.9 billion in stipends for village elders in the 2026/27 budget was met with heckling from MPs during his budget presentation."}, {"label": "Mbadi Senate Scrutiny", "strength": 10.78, "growth": 0.0, "mentions": 8, "description": "CS John Mbadi faces tough questioning in Senate over government economic plans, defending President Ruto's administration amid clashes with Senator Sifuna and legal challenges over budget proposals."}, {"label": "Budget Statement Presentation", "strength": 9.57, "growth": 0.5, "mentions": 5, "description": "Cabinet Secretary John Mbadi delivered the FY 2026/27 Budget Statement on 11th June 2026, themed around sustaining bottom-up economic transformation for inclusive growth amid global uncertainty."}, {"label": "Mbadi Budget Leadership", "strength": 9.02, "growth": 0.25, "mentions": 9, "description": "Social media posts highlight Treasury CS John Mbadi's budget presentation and economic management, with supporters praising his leadership style, clear communication, and even touting him as a future presidential candidate."}, {"label": "Treasury Capital Markets Leadership", "strength": 7.0, "growth": 0.33, "mentions": 7, "description": "CS John Mbadi is being credited with spearheading key capital market milestones including a housing sustainability bond listing and Kenya Pipeline Company IPO, while also weighing PAYE band adjustments that could cost the exchequer Ksh 35 billion in revenue."}, {"label": "State Privatization Push", "strength": 5.0, "growth": 0.5, "mentions": 5, "description": "CS John Mbadi is driving Kenya's privatization agenda, highlighted by the Kenya Pipeline Company's IPO which transferred 65% ownership to the public, transitioning it from a government entity to a commercially run PLC."}, {"label": "Safaricom Shares Debate", "strength": 4.96, "growth": 0.0, "mentions": 4, "description": "CS John Mbadi defends the government's plan to sell its Safaricom shares, pushing back against criticism from Ndindi Nyoro and warning that failure to offload the shares would force the government to take loans."}, {"label": "Africa Investment Forum", "strength": 4.0, "growth": 4.0, "mentions": 4, "description": "John Mbadi served as keynote speaker at the inaugural Resilient Africa Forum in Washington, D.C., where senior decision-makers gathered to discuss Africa's growth and investment priorities alongside the IMF/World Bank Spring Meetings."}, {"label": "PAYE Relief Debate", "strength": 3.64, "growth": 1.0, "mentions": 3, "description": "CS John Mbadi's proposed PAYE tax relief for lower-income Kenyan workers has sparked debate, with the Kenya Bankers Association supporting the move but warning that limiting relief to lower earners could create a revenue gap and fail to broadly stimulate economic activity."}, {"label": "Fiscal Centralization Reforms", "strength": 3.25, "growth": 1.0, "mentions": 3, "description": "CS Mbadi announced that from July 1, 2026, the Treasury Single Account will be expanded to counties and all procurement must go through the e-government system with no exemptions."}, {"label": "Budget Public Expectations", "strength": 3.04, "growth": 1.0, "mentions": 3, "description": "Ahead of the 2026/27 budget reading, CS Mbadi faces public calls from ordinary citizens to prioritize their welfare, even as he opens proceedings with a tribute to Raila Odinga."}], "risks": ["NTV Kenya (influence score 2,318.4) and _evansmwirigi (score 1,584.1) are the two highest-reach negative sentiment contributors with scores of -8.0 and -17.0 respectively, meaning critical voices are amplified by the most influential accounts in Mbadi's conversation.", "The Finance Bill 2026 Debate narrative is actively growing (growth_rate 0.5) while simultaneously drawing criticism over Mbadi's communication approach, creating a live, escalating vulnerability tied directly to his core Treasury mandate.", "The ODM Internal Power Struggle narrative — featuring public clashes with Edwin Sifuna and Ruth Odinga over dynastic party control — has 11 mentions and a non-zero growth rate, risking the perception that Mbadi is politically isolated within his own party base at a critical budget period.", "TikTok accounts for 118 of 544 total mentions (roughly 22%), a platform skewing toward younger, protest-sympathetic audiences, and rtc_media on that ecosystem carries a sentiment contribution of -15.0, suggesting concentrated negative framing is reaching a demographic already sensitised to anti-government budget messaging from the 2024 Finance Bill protests.", "The Mbadi Leadership Scrutiny narrative explicitly includes accusations of illegal conduct alongside competence questions, and with a growth_rate of 0.5 it is still expanding, meaning reputational damage framed in legal or ethical terms — not just policy disagreement — is gaining traction."], "opportunities": ["The 'Budget Allocation Proposals' narrative has a growth_rate of 1.0 (the highest in the dataset) despite only 3 mentions, signaling an early-stage breakout story around the KSh 784.5 billion education and KSh 9.4 billion landless settlement allocations that warrants immediate amplification before competitors frame it.", "money254hq carries the highest influence score (2431.6) with a rare positive sentiment contribution (+2.0), making them the single most valuable influencer to engage or amplify given that most high-reach handles are dragging sentiment negative.", "LinkedIn has 150 mentions — the second-highest platform volume — yet none of the top influence drivers are LinkedIn accounts, representing an underused channel where Mbadi's technocratic budget and Infrastructure Fund narratives could organically resonate with a professional, policy-literate audience.", "The 'Infrastructure Fund Explanation' narrative has a 0.5 growth_rate and only 5 mentions, meaning Mbadi's message about financing infrastructure through pension funds and private capital rather than taxes is gaining traction but remains under-distributed and could be scaled to counter the dominant negative economic framing.", "With 52.8% neutral sentiment across 544 mentions, a large persuadable audience exists that has not yet formed a negative view of Mbadi, and targeted content clarifying Finance Bill 2026 misconceptions — directly addressing the dropped taxes and no new MPesa charges points already in his narrative — could convert this bloc before negative influencers like _evansmwirigi (sentiment contribution -17.0) solidify opinion."], "trends": ["The Finance Bill 2026 narrative carries a 0.5 growth rate with only 5 mentions so far, suggesting it is in early amplification and could rapidly dominate discourse as the bill advances through Parliament and public scrutiny intensifies.", "The Infrastructure Fund Explanation narrative (growth rate 0.5, 5 mentions) is gaining traction as Mbadi frames private capital from pension funds and banks as an alternative to taxation — a framing that could either build fiscal credibility or trigger backlash if pension fund risks are spotlighted.", "The ODM Internal Power Struggle narrative around Mbadi's clash with Sifuna and Ruth Odinga has 11 mentions but a relatively low growth rate of 0.2, making it a slow-burn risk that could accelerate sharply if Raila Odinga publicly weighs in or the dispute escalates ahead of party elections.", "TikTok accounts for 118 of 544 total mentions — the second-highest platform — yet influence driver scores are dominated by YouTube and news handles, indicating an under-mapped TikTok opinion ecosystem around Mbadi that warrants dedicated monitoring for sentiment shifts.", "The top influence driver _evansmwirigi carries the highest negative sentiment contribution (-17.0) among named handles, flagging a concentrated source of reputational risk that could disproportionately shape public perception if his content on Mbadi gains further algorithmic reach."]},
}


def _run_report_job(job_id: str, name: str) -> None:
    db = SessionLocal()
    try:
        politician = _ensure_politician(db, name)
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=210)
        report = run_pipeline(db, politician, period="live-demo", window_start=window_start, window_end=window_end)
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


@app.get("/api/health")
def health():
    return {"ok": True}

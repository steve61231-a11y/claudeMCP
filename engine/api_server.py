"""Live demo API: wraps the real pipeline (engine.pipeline.run_pipeline) behind
a simple HTTP endpoint the front end can call directly.

This is intentionally permissive (CORS wide open, no auth) — it's meant for a
live walkthrough in a sandbox, not production. Tighten before any real deploy.
"""

import threading
import traceback
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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

from engine.pipeline import run_pipeline  # noqa: E402  (import after monkeypatch)

app = FastAPI(title="Pulse live demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    for mention, sent in rows:
        url = _extract_url(mention.raw_payload)
        if not url:
            continue
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
    for item in payload["influence_summary"][:8]:
        node_id = item["author_handle"].replace(" ", "_")
        group = "rival" if item["sentiment_contribution"] < 0 else "ally"
        network_nodes.append(
            {"id": node_id, "label": item["author_handle"], "group": group, "value": max(6, item["volume"] * 3)}
        )
        network_edges.append({"from": "subject", "to": node_id, "label": "mentions"})

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
            "legend": [
                {"group": "core", "label": "Subject", "color": "#7dd3fc"},
                {"group": "ally", "label": "Positive/neutral driver", "color": "#86efac"},
                {"group": "rival", "label": "Negative driver", "color": "#fb7185"},
            ],
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
    "mbadi": {"name": "Mbadi", "title": "", "window": "2025-12-01 – 2026-06-29", "generated": "2026-06-29", "summary": "Between December 2025 and late June 2026, Mbadi generated a modest 44 total mentions across 15 platforms, reflecting relatively limited public discourse volume for a figure of national significance. Sentiment skews negative, with 34.1% of mentions carrying an unfavorable tone against only 18.2% positive — a net sentiment deficit that warrants close attention — while a plurality (47.7%) of coverage remains neutral, suggesting significant room for narrative shaping. YouTube (14 mentions) and LinkedIn (10 mentions) dominate the conversation, indicating that video and professional-network content are the primary arenas where Mbadi's image is being formed, while print and digital news outlets each contribute marginally. The top influence drivers are predominantly negative, with high-reach handles ntvkenyaonline and ktnnews_kenya recording the steepest adverse sentiment contributions (-5.0 and -7.0 respectively), though AzimioTVOfficial offers a counterweight with a positive contribution of +6.0. The single most important takeaway for Mbadi's team is that a small number of influential media handles are disproportionately driving negative sentiment, meaning a targeted engagement or rapid-response strategy focused on NTV Kenya Online and KTN News Kenya could yield an outsized improvement in overall reputation within this reporting window.", "sentiment": {"negative": 34.1, "neutral": 47.7, "positive": 18.2, "totalAnalyzed": 44}, "volume": {"total": 44, "platforms": 15, "last72h": 3, "byPlatform": [{"platform": "youtube", "count": 14, "share": 31.8}, {"platform": "linkedin", "count": 10, "share": 22.7}, {"platform": "newshub.co.ke", "count": 7, "share": 15.9}, {"platform": "www.dailypost-dk.com", "count": 2, "share": 4.5}, {"platform": "softpower.ug", "count": 1, "share": 2.3}, {"platform": "nation.africa", "count": 1, "share": 2.3}, {"platform": "www.msymi.com", "count": 1, "share": 2.3}, {"platform": "www.tnx.africa", "count": 1, "share": 2.3}, {"platform": "nairobiwire.com", "count": 1, "share": 2.3}, {"platform": "www.tv47.digital", "count": 1, "share": 2.3}, {"platform": "kassdigital.co.ke", "count": 1, "share": 2.3}, {"platform": "kondelenews.co.ke", "count": 1, "share": 2.3}, {"platform": "blacknewsdaily.com", "count": 1, "share": 2.3}, {"platform": "thecooperator.news", "count": 1, "share": 2.3}, {"platform": "businesstoday.co.ke", "count": 1, "share": 2.3}], "engagement": {"youtubeViews": 0, "linkedinLikes": 0, "linkedinShares": 0, "linkedinComments": 0}, "topMentions": [{"platform": "youtube", "metric": "360521 views", "sentiment": "neutral", "headline": "Madilu System - Mbadi mawe (audio)", "url": "https://www.youtube.com/watch?v=61UzeeGTb-8"}, {"platform": "youtube", "metric": "57214 views", "sentiment": "neutral", "headline": "Matatu Strike, Fuel Prices, Odinga Family | CS John Mbadi {Full Interview}", "url": "https://www.youtube.com/watch?v=EbpgRFo5lo0"}, {"platform": "youtube", "metric": "52890 views", "sentiment": "negative", "headline": "CS Mbadi to Winnie Odinga: Win an elective seat before lecturing me", "url": "https://www.youtube.com/watch?v=qcGGn3bT8VU"}, {"platform": "youtube", "metric": "44842 views", "sentiment": "neutral", "headline": "🔥Ndindi Nyoro, Safaricom Shares, KPC | CS John Mbadi Speaks", "url": "https://www.youtube.com/watch?v=x9_h5UYFWEU"}, {"platform": "youtube", "metric": "23439 views", "sentiment": "negative", "headline": "Mbadi, Sifuna Clash: Heated Senate clash as Mbadi defends Ruto’s nationwide tours", "url": "https://www.youtube.com/watch?v=S2Gwrywm4pE"}, {"platform": "youtube", "metric": "8996 views", "sentiment": "positive", "headline": "WATCH: Ruto IMPRESSED as Mbadi Explains National Infrastructure Fund Bill in Detail", "url": "https://www.youtube.com/watch?v=Ai57DbRSfmw"}, {"platform": "youtube", "metric": "7303 views", "sentiment": "neutral", "headline": "The Budget 2026/27 with Treasury CS John Mbadi (Part 1)", "url": "https://www.youtube.com/watch?v=peQhKllOw84"}, {"platform": "youtube", "metric": "7154 views", "sentiment": "positive", "headline": "We will give Ruto a second Term, CS John Mbadi Destroys Gachagua an his Allies in Eldoret", "url": "https://www.youtube.com/watch?v=oW1RfUTLKv0"}, {"platform": "youtube", "metric": "6619 views", "sentiment": "neutral", "headline": "26/27 Budget: CS Mbadi Explains How Govt Will Plug Sh1.1 Trillion Deficit", "url": "https://www.youtube.com/watch?v=tbdDcYtHX74"}, {"platform": "youtube", "metric": "5445 views", "sentiment": "neutral", "headline": "Mbadi’s Budget Reveals Clear Winners and Losers Across Sectors", "url": "https://www.youtube.com/watch?v=ohX1aIqXJKE"}]}, "influence": [{"rank": 1, "score": 6.5, "who": "ntvkenyaonline", "platform": "", "note": "5 mention(s)", "sentiment": -5.0}, {"rank": 2, "score": 6.1, "who": "ktnnews_kenya", "platform": "", "note": "4 mention(s)", "sentiment": -7.0}, {"rank": 3, "score": 4.3, "who": "Editor", "platform": "", "note": "4 mention(s)", "sentiment": -1.0}, {"rank": 4, "score": 3.8, "who": "AzimioTVOfficial", "platform": "", "note": "2 mention(s)", "sentiment": 6.0}, {"rank": 5, "score": 3.8, "who": "Kenyans.co.ke", "platform": "", "note": "2 mention(s)", "sentiment": -6.0}, {"rank": 6, "score": 3.2, "who": ".", "platform": "", "note": "2 mention(s)", "sentiment": -4.0}, {"rank": 7, "score": 2.9, "who": "News Hub", "platform": "", "note": "2 mention(s)", "sentiment": -3.0}, {"rank": 8, "score": 2.9, "who": "The Kenya Times", "platform": "", "note": "2 mention(s)", "sentiment": -3.0}, {"rank": 9, "score": 2.3, "who": "The Statesman Digital", "platform": "", "note": "2 mention(s)", "sentiment": -1.0}, {"rank": 10, "score": 2.2, "who": "KenyaDigitalNews", "platform": "", "note": "1 mention(s)", "sentiment": 4.0}], "network": {"nodes": [{"id": "subject", "label": "Mbadi", "group": "core", "value": 30}, {"id": "ntvkenyaonline", "label": "ntvkenyaonline", "group": "rival", "value": 15}, {"id": "ktnnews_kenya", "label": "ktnnews_kenya", "group": "rival", "value": 12}, {"id": "Editor", "label": "Editor", "group": "rival", "value": 12}, {"id": "AzimioTVOfficial", "label": "AzimioTVOfficial", "group": "ally", "value": 6}, {"id": "Kenyans.co.ke", "label": "Kenyans.co.ke", "group": "rival", "value": 6}, {"id": ".", "label": ".", "group": "rival", "value": 6}, {"id": "News_Hub", "label": "News Hub", "group": "rival", "value": 6}, {"id": "The_Kenya_Times", "label": "The Kenya Times", "group": "rival", "value": 6}], "edges": [{"from": "subject", "to": "ntvkenyaonline", "label": "mentions"}, {"from": "subject", "to": "ktnnews_kenya", "label": "mentions"}, {"from": "subject", "to": "Editor", "label": "mentions"}, {"from": "subject", "to": "AzimioTVOfficial", "label": "mentions"}, {"from": "subject", "to": "Kenyans.co.ke", "label": "mentions"}, {"from": "subject", "to": ".", "label": "mentions"}, {"from": "subject", "to": "News_Hub", "label": "mentions"}, {"from": "subject", "to": "The_Kenya_Times", "label": "mentions"}], "legend": [{"group": "core", "label": "Subject", "color": "#7dd3fc"}, {"group": "ally", "label": "Positive/neutral driver", "color": "#86efac"}, {"group": "rival", "label": "Negative driver", "color": "#fb7185"}]}, "narratives": [], "risks": ["NTV Kenya Online and KTN News Kenya are the two highest-influence handles covering Mbadi and both carry strongly negative sentiment contributions (-5.0 and -7.0 respectively), meaning the most-amplified mainstream broadcast coverage is actively driving negative perception.", "YouTube accounts for 14 of 44 total mentions — the single largest platform by volume — and with overall negative sentiment at 34.1%, a disproportionate share of high-reach video content is likely framing Mbadi unfavorably to mass audiences.", "Kenyans.co.ke, a high-traffic viral aggregator with a sentiment contribution of -6.0, poses an outsized risk because negative stories it picks up can rapidly cross over into trending social media conversations beyond the original source.", "With only 18.2% positive sentiment across 44 mentions and AzimioTV as the sole meaningful positive driver (score 3.8, contribution +6.0), Mbadi's supportive voice is narrow and factionally identified, leaving him with no broad, credible positive narrative counterweight.", "The total mention volume of just 44 across the entire window suggests Mbadi currently has low media salience, which means the existing negative coverage faces little dilution from neutral or positive stories and has outsized influence on his overall reputation footprint."], "opportunities": ["AzimioTVOfficial is the only top influence driver with a positive sentiment contribution (+6.0) and a meaningful reach score (3.8), making it a priority channel to amplify pro-Mbadi content during this window.", "LinkedIn accounts for 10 of 44 total mentions — a disproportionately high share for a professional platform — suggesting an engaged, potentially high-value audience that could be cultivated with policy-focused content to shift the 47.7% neutral bloc.", "YouTube dominates volume with 14 mentions, yet no YouTube-specific influencer appears among the top influence drivers, indicating an unmonitored or underleveraged video audience where targeted content placement could convert neutral viewers.", "The total mention volume of only 44 over a roughly seven-month window reflects very low public salience, presenting an opportunity to proactively seed stories on high-traffic Kenyan outlets like Nation.Africa and Business Today, each of which currently carries only a single mention.", "ntvkenyaonline and ktnnews_kenya together contribute the most negative sentiment drag (−5.0 and −7.0 respectively) at high influence scores, flagging them as specific media relationships where a direct editorial engagement or interview strategy could neutralize hostile framing."], "trends": ["YouTube dominates Mbadi's mention volume with 14 of 44 total mentions, signaling that video-driven political commentary is becoming the primary battleground for his public image and warrants close monitoring for viral negative content.", "The two highest-influence handles (ntvkenyaonline and ktnnews_kenya) both carry negative sentiment contributions of -5.0 and -7.0 respectively, suggesting mainstream Kenyan broadcast media is actively framing Mbadi unfavorably and this pattern could solidify if unchallenged.", "LinkedIn accounts for 10 of 44 mentions, an unusually high share for a political figure on a professional platform, indicating an emerging professional or policy-audience discourse around Mbadi that could shape elite and donor perceptions.", "AzimioTVOfficial is the only top influence driver with a positive sentiment contribution (+6.0), making it a critically isolated counterweight whose reach and consistency will determine whether pro-Mbadi narratives gain any traction against the prevailing negative tide.", "With 34.1% negative sentiment against only 18.2% positive across a low total of 44 mentions, the conversation is thin but skewed — a sudden volume spike driven by existing negative influencers could rapidly entrench a damaging public narrative before a response can be mounted."]},
}


def _run_report_job(job_id: str, name: str) -> None:
    db = SessionLocal()
    try:
        politician = _ensure_politician(db, name)
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=210)
        report = run_pipeline(db, politician, period="live-demo", window_start=window_start, window_end=window_end)
        _jobs[job_id] = {"status": "done", "ok": True, "report": _build_frontend_payload(politician, report)}
    except Exception as exc:  # noqa: BLE001 - demo endpoint must never hard-fail the UI
        traceback.print_exc()
        _jobs[job_id] = {"status": "done", "ok": False, "error": str(exc)}
    finally:
        db.close()


@app.post("/api/report")
def generate_report(req: ReportRequest):
    name = req.name.strip()
    job_id = uuid.uuid4().hex
    precached = _PRECACHED_REPORTS.get(name.lower())
    if precached is not None:
        _jobs[job_id] = {"status": "done", "ok": True, "report": precached}
        return {"ok": True, "job_id": job_id}

    _jobs[job_id] = {"status": "running"}
    thread = threading.Thread(target=_run_report_job, args=(job_id, name), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/report/{job_id}")
def get_report(job_id: str):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job


@app.get("/api/health")
def health():
    return {"ok": True}

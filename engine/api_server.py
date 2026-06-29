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
    "mbadi": {"name": "Mbadi", "title": "", "window": "2025-12-01 \u2013 2026-06-29", "generated": "2026-06-29", "summary": "Over the December 2025 to June 2026 window, John Mbadi's media presence across 82 analyzed mentions reflects a deeply polarized yet largely neutral public perception, with positive and negative sentiment tied at 24.4% each and a dominant neutral majority of 51.2%. The leading narrative \u2014 Mbadi's Fiscal Leadership \u2014 accounts for nearly a third of all mentions and centers on his role navigating Kenya's contentious tax reform debates, Finance Bill protests, and debt management scrutiny, signaling that his identity as Treasury Cabinet Secretary is firmly established but contested. A fast-rising secondary narrative around the FY 2026/27 Budget presentation (growth rate of 4.0) suggests growing public and media interest in his concrete policy outputs, particularly youth employment allocations and village elder stipends. Coverage is heavily concentrated on LinkedIn (37 mentions) and YouTube (23 mentions), indicating that discourse is largely driven by professional and civic audiences rather than mainstream print media. Critically, the top influence drivers \u2014 Kenyans.co.ke, Martin Wachira, and KBCChannel1News \u2014 all carry negative sentiment contributions, meaning the most impactful voices in Mbadi's media ecosystem are actively shaping an unfavorable narrative. The single most important takeaway for his team is that while neutral sentiment provides a buffer, the negative influence of high-scoring handles demands a proactive counter-narrative strategy, particularly on digital platforms where his exposure is greatest.", "sentiment": {"negative": 24.4, "neutral": 51.2, "positive": 24.4, "totalAnalyzed": 82}, "volume": {"total": 82, "platforms": 16, "last72h": 8, "byPlatform": [{"platform": "linkedin", "count": 37, "share": 45.1}, {"platform": "youtube", "count": 23, "share": 28.0}, {"platform": "newshub.co.ke", "count": 7, "share": 8.5}, {"platform": "news", "count": 2, "share": 2.4}, {"platform": "www.dailypost-dk.com", "count": 2, "share": 2.4}, {"platform": "softpower.ug", "count": 1, "share": 1.2}, {"platform": "nation.africa", "count": 1, "share": 1.2}, {"platform": "www.msymi.com", "count": 1, "share": 1.2}, {"platform": "www.tnx.africa", "count": 1, "share": 1.2}, {"platform": "nairobiwire.com", "count": 1, "share": 1.2}, {"platform": "www.tv47.digital", "count": 1, "share": 1.2}, {"platform": "kassdigital.co.ke", "count": 1, "share": 1.2}, {"platform": "kondelenews.co.ke", "count": 1, "share": 1.2}, {"platform": "blacknewsdaily.com", "count": 1, "share": 1.2}, {"platform": "thecooperator.news", "count": 1, "share": 1.2}, {"platform": "businesstoday.co.ke", "count": 1, "share": 1.2}], "engagement": {"youtubeViews": 0, "linkedinLikes": 0, "linkedinShares": 0, "linkedinComments": 0}, "topMentions": [{"platform": "youtube", "metric": "360519 views", "sentiment": "neutral", "headline": "Madilu System - Mbadi mawe (audio)", "url": "https://www.youtube.com/watch?v=61UzeeGTb-8"}, {"platform": "youtube", "metric": "315160 views", "sentiment": "neutral", "headline": "Inside The Decision: A Conversation with CS, John Mbadi, EGH", "url": "https://www.youtube.com/watch?v=w-tLjonSGGc"}, {"platform": "youtube", "metric": "83167 views", "sentiment": "negative", "headline": "JKL | ODM KICKS OUT SIFUNA| CS JOHN MBADI |", "url": "https://www.youtube.com/watch?v=i2ey9YTbuqg"}, {"platform": "youtube", "metric": "57214 views", "sentiment": "neutral", "headline": "Matatu Strike, Fuel Prices, Odinga Family | CS John Mbadi {Full Interview}", "url": "https://www.youtube.com/watch?v=EbpgRFo5lo0"}, {"platform": "youtube", "metric": "52890 views", "sentiment": "negative", "headline": "CS Mbadi to Winnie Odinga: Win an elective seat before lecturing me", "url": "https://www.youtube.com/watch?v=qcGGn3bT8VU"}, {"platform": "youtube", "metric": "50883 views", "sentiment": "neutral", "headline": "Hon. Mbadi - Odosh Jasuba (Official Audio)", "url": "https://www.youtube.com/watch?v=wuZcvN-Mhf4"}, {"platform": "youtube", "metric": "44842 views", "sentiment": "neutral", "headline": "\ud83d\udd25Ndindi Nyoro, Safaricom Shares, KPC | CS John Mbadi Speaks", "url": "https://www.youtube.com/watch?v=x9_h5UYFWEU"}, {"platform": "youtube", "metric": "28191 views", "sentiment": "neutral", "headline": "CS John Mbadi: There is no easy budget. This year\u2019s budget is even more challenging.", "url": "https://www.youtube.com/watch?v=IPDgl1KxlE0"}, {"platform": "youtube", "metric": "25295 views", "sentiment": "negative", "headline": "SHUT UP !YOU CANT LECTURE ME ON ECONOMY!Angry CS Mbadi Explodes at Babu Owino", "url": "https://www.youtube.com/watch?v=6szGYYuMoWM"}, {"platform": "youtube", "metric": "23439 views", "sentiment": "negative", "headline": "Mbadi, Sifuna Clash: Heated Senate clash as Mbadi defends Ruto\u2019s nationwide tours", "url": "https://www.youtube.com/watch?v=S2Gwrywm4pE"}]}, "influence": [{"rank": 1, "score": 6.7, "who": "Kenyans.co.ke", "platform": "", "note": "4 mention(s)", "sentiment": -9.0}, {"rank": 2, "score": 6.1, "who": "Martin Wachira", "platform": "", "note": "4 mention(s)", "sentiment": -7.0}, {"rank": 3, "score": 5.9, "who": "KBCChannel1News", "platform": "", "note": "5 mention(s)", "sentiment": -3.0}, {"rank": 4, "score": 5.5, "who": "The Kenya Times", "platform": "", "note": "4 mention(s)", "sentiment": 5.0}, {"rank": 5, "score": 4.9, "who": "The Statesman Digital", "platform": "", "note": "4 mention(s)", "sentiment": -3.0}, {"rank": 6, "score": 4.3, "who": "Editor", "platform": "", "note": "4 mention(s)", "sentiment": -1.0}, {"rank": 7, "score": 3.0, "who": "KenyaDigitalNews", "platform": "", "note": "3 mention(s)", "sentiment": 0.0}, {"rank": 8, "score": 2.9, "who": "News Hub", "platform": "", "note": "2 mention(s)", "sentiment": -3.0}, {"rank": 9, "score": 2.6, "who": "UzalendoNews", "platform": "", "note": "2 mention(s)", "sentiment": 2.0}, {"rank": 10, "score": 2.3, "who": ".", "platform": "", "note": "2 mention(s)", "sentiment": -1.0}], "network": {"nodes": [{"id": "subject", "label": "Mbadi", "group": "core", "value": 30}, {"id": "Kenyans.co.ke", "label": "Kenyans.co.ke", "group": "rival", "value": 12}, {"id": "Martin_Wachira", "label": "Martin Wachira", "group": "rival", "value": 12}, {"id": "KBCChannel1News", "label": "KBCChannel1News", "group": "rival", "value": 15}, {"id": "The_Kenya_Times", "label": "The Kenya Times", "group": "ally", "value": 12}, {"id": "The_Statesman_Digital", "label": "The Statesman Digital", "group": "rival", "value": 12}, {"id": "Editor", "label": "Editor", "group": "rival", "value": 12}, {"id": "KenyaDigitalNews", "label": "KenyaDigitalNews", "group": "ally", "value": 9}, {"id": "News_Hub", "label": "News Hub", "group": "rival", "value": 6}], "edges": [{"from": "subject", "to": "Kenyans.co.ke", "label": "mentions"}, {"from": "subject", "to": "Martin_Wachira", "label": "mentions"}, {"from": "subject", "to": "KBCChannel1News", "label": "mentions"}, {"from": "subject", "to": "The_Kenya_Times", "label": "mentions"}, {"from": "subject", "to": "The_Statesman_Digital", "label": "mentions"}, {"from": "subject", "to": "Editor", "label": "mentions"}, {"from": "subject", "to": "KenyaDigitalNews", "label": "mentions"}, {"from": "subject", "to": "News_Hub", "label": "mentions"}], "legend": [{"group": "core", "label": "Subject", "color": "#7dd3fc"}, {"group": "ally", "label": "Positive/neutral driver", "color": "#86efac"}, {"group": "rival", "label": "Negative driver", "color": "#fb7185"}]}, "narratives": [{"label": "Mbadi's Fiscal Leadership", "strength": 25.0, "growth": 0.08, "mentions": 25, "description": "As Treasury Cabinet Secretary, John Mbadi is at the center of Kenya's contentious fiscal debates, navigating tax reform proposals, constitutional budget compliance orders, and public protests over the Finance Bill while defending the government's debt management strategy."}, {"label": "Kenya Budget 2026/27", "strength": 6.0, "growth": 4.0, "mentions": 6, "description": "Cabinet Secretary John Mbadi presented Kenya's FY 2026/27 Budget to Parliament, highlighting key allocations including 2 billion Ksh for youth employment and 3.9 billion Ksh in stipends for village elders."}, {"label": "Mbadi Under Scrutiny", "strength": 4.0, "growth": 0.0, "mentions": 4, "description": "CS John Mbadi faces public criticism and political sparring over his authority, stance on corruption, and governance approach."}], "risks": ["The 'Mbadi Under Scrutiny' narrative, while currently low in mention count (4), shows zero growth rate suggesting it has plateaued at a persistent baseline of criticism around corruption stance and governance authority that refuses to dissipate.", "Kenyans.co.ke and Martin Wachira are the two highest-influence drivers with sentiment contributions of -9.0 and -7.0 respectively, meaning the most amplified voices in Mbadi's coverage are actively pulling his reputation negative.", "The 'Mbadi's Fiscal Leadership' narrative dominates with 25 mentions and a strength score of 25.0, but its context \u2014 tax reform protests and Finance Bill controversy \u2014 means the single largest story associated with him is rooted in public opposition and contention.", "The 'Kenya Budget 2026/27' narrative has an exceptionally high growth rate of 4.0, the fastest-growing story in the dataset, and given the broader fiscal controversy context, rapid amplification of budget coverage carries elevated risk of negative pile-on as scrutiny increases.", "With 24.4% negative sentiment exactly matching the 24.4% positive sentiment across 82 mentions, Mbadi has no net positive reputation buffer, leaving him vulnerable to any single negative event tipping the balance into a net-negative public perception."], "opportunities": ["The 'Kenya Budget 2026/27' narrative has the highest growth rate in the dataset at 4.0, meaning amplifying Mbadi's specific budget allocations \u2014 such as the 2 billion Ksh youth employment fund and 3.9 billion Ksh for village elders \u2014 on LinkedIn (37 mentions, the top platform) could accelerate momentum while sentiment is still forming.", "The Kenya Times is the only top influence driver with a positive sentiment contribution (+5.0), making it a priority channel to seed favorable fiscal leadership content and counter the negative drag from Kenyans.co.ke (-9.0) and Martin Wachira (-7.0).", "LinkedIn accounts for 45% of total mentions (37 of 82) yet no LinkedIn-specific influencer appears in the top influence drivers list, suggesting an untapped opportunity to identify and engage existing LinkedIn advocates already driving organic volume.", "nation.africa, businesstoday.co.ke, and softpower.ug each have only 1 mention despite being credible outlets with broad reach, representing clear placement gaps for budget-focused op-eds or interviews tied to the high-growth Budget 2026/27 narrative.", "With 51.2% of sentiment currently neutral, there is a persuadable majority audience that has not formed a negative view of Mbadi's fiscal leadership, making a targeted factual content push \u2014 anchored on debt management outcomes and budget allocations \u2014 a viable conversion opportunity before the window closes in June 2026."], "trends": ["The 'Kenya Budget 2026/27' narrative is recording a growth rate of 4.0 \u2014 the highest of any narrative \u2014 despite only 6 mentions, signaling it is likely to become the dominant conversation around Mbadi as budget scrutiny intensifies in Parliament.", "LinkedIn accounts for 45% of all platform mentions (37 of 82), suggesting Mbadi's fiscal messaging is gaining traction among professional and business audiences, a platform dynamic worth tracking for elite opinion formation.", "Top influence drivers \u2014 Kenyans.co.ke, Martin Wachira, and KBCChannel1News \u2014 all carry negative sentiment contributions, meaning the most impactful voices are collectively pulling Mbadi's public image downward despite a balanced overall sentiment split.", "The 'Mbadi Under Scrutiny' narrative has a growth rate of 0.0 currently, but its co-existence alongside active public protests over the Finance Bill creates conditions for a sudden spike if a concrete corruption allegation or governance failure crystallises.", "YouTube (23 mentions) is the second-largest platform and the leading non-professional channel, indicating that video-format criticism or commentary on Mbadi's fiscal decisions is building reach beyond text-based media and could amplify negative narratives rapidly."]},
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

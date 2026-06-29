"""Live demo API: wraps the real pipeline (engine.pipeline.run_pipeline) behind
a simple HTTP endpoint the front end can call directly.

This is intentionally permissive (CORS wide open, no auth) — it's meant for a
live walkthrough in a sandbox, not production. Tighten before any real deploy.
"""

import traceback
from datetime import datetime, timedelta

from fastapi import FastAPI
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
        "risks": [],
        "opportunities": [],
        "trends": [],
    }


@app.post("/api/report")
def generate_report(req: ReportRequest):
    db = SessionLocal()
    try:
        politician = _ensure_politician(db, req.name.strip())
        window_end = datetime.utcnow()
        window_start = window_end - timedelta(days=210)
        report = run_pipeline(db, politician, period="live-demo", window_start=window_start, window_end=window_end)
        return {"ok": True, "report": _build_frontend_payload(politician, report)}
    except Exception as exc:  # noqa: BLE001 - demo endpoint must never hard-fail the UI
        traceback.print_exc()
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()


@app.get("/api/health")
def health():
    return {"ok": True}

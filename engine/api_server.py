"""Live demo API: wraps the real pipeline (engine.pipeline.run_pipeline) behind
a simple HTTP endpoint the front end can call directly.

Hardening knobs (engine/config.py): PULSE_API_KEY gates /api/report with an
X-API-Key header, ALLOWED_ORIGINS restricts CORS, and report submissions are
rate-limited per client IP. All default to permissive for local dev only.
"""

import hmac
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from engine import llm as _llm
from engine import health as health_mod
from engine import stages as stages_mod
from engine.config import settings
from engine.db.models import MentionSentiment, Politician, RawMention
from engine.db.session import SessionLocal
from engine.pipeline import run_ingestion, run_pipeline

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
    # Security posture warning: outside local-dev, an unset API key means every
    # data/admin endpoint is open to anyone who finds the URL.
    if not settings.pulse_api_key and not settings.is_local_dev:
        print("SECURITY WARNING: PULSE_API_KEY is not set — the API is UNAUTHENTICATED "
              "and open to the public. Set PULSE_API_KEY to gate report/network/admin endpoints.")
    if _cors_origins == ["*"] and not settings.is_local_dev:
        print("SECURITY NOTE: ALLOWED_ORIGINS is unset (CORS = '*'). Set it to your "
              "site origin(s) to restrict cross-site browser access.")


_cors_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()] or ["*"]


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Baseline hardening headers (safe for the self-hosted inline frontend)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalise_key(value: str | None) -> str:
    """A key as a person meant it: no surrounding whitespace, no wrapping quotes.

    The page trims what it sends, and the check compared that against the
    configured value EXACTLY — so a PULSE_API_KEY pasted into the host's
    variables with a trailing space, a line break or the quotes from an
    .env file could never be matched by anything the page can send. The
    operator was told "it rejected the key" about two values that were the
    same key.
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


#: Why a key was refused, in words the operator can act on. Says which of the
#: two cases it is — the page cannot otherwise tell "this browser has no key
#: saved" from "the key it has is not the engine's" — and nothing about the
#: real key: this answer goes to anyone who asks.
KEY_MISSING = "missing API key: this request carried no x-api-key"
KEY_MISMATCH = "invalid API key: an x-api-key was sent and does not match PULSE_API_KEY"


def _require_api_key(x_api_key: str | None) -> None:
    """Enforced only when PULSE_API_KEY is configured, so local dev and the
    existing frontend keep working until the key is rolled out to both sides."""
    expected = _normalise_key(settings.pulse_api_key)
    if not expected:
        return
    sent = _normalise_key(x_api_key)
    if not sent:
        raise HTTPException(status_code=401, detail=KEY_MISSING)
    # Constant-time, so the comparison does not leak how much of a guess
    # was right.
    if not hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=401, detail=KEY_MISMATCH)


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

#: How long a "running" job may go SILENT before it is presumed dead.
#:
#: `_evict_stale_jobs` deliberately never evicts a running job, however old,
#: because a full-stack issue map can legitimately outlive an hour. But a job
#: whose thread died — a deploy, a container restart, an OOM, an exception
#: outside the guarded block — also stays "running" forever, and
#: `_inflight_job` then hands every new request for that subject back to the
#: corpse. A live run showed "nothing has arrived for 587 minutes" and could
#: not be restarted at all: the subject was locked by a run that had been dead
#: for ten hours.
#:
#: Age is the wrong test; SILENCE is the right one. A working run publishes a
#: stage or a section continuously, so 30 minutes without a single update is
#: far beyond the slowest legitimate stage (the analyst deadline is 15) while
#: being nowhere near the hours a real run can take.
_JOB_SILENCE_SECONDS = 1800


def _evict_stale_jobs() -> None:
    """Drop finished jobs older than the TTL.

    A job that is STILL RUNNING is never evicted, however old it is. Evicting
    one deletes the only handle its client has on work that is actively
    happening, and the poll then 404s on a run that is going perfectly well.
    That was survivable when a run took minutes; a full-stack issue map —
    dozens of source queries, an eighty-probe discovery sweep and four
    analysts on a rate-limited backend — can outlive an hour, which made it
    reachable.
    """
    cutoff = time.time() - _JOB_TTL_SECONDS
    stale = [
        jid for jid, job in _jobs.items()
        if job.get("created_at", 0) < cutoff and job.get("status") != "running"
    ]
    for job_id in stale:
        _jobs.pop(job_id, None)


class ReportRequest(BaseModel):
    name: str
    # Multi-domain: person | politician | organization | ministry | business |
    # individual. Defaults to politician for backward compatibility.
    type: str = "politician"
    #: How many days back to analyse. None uses REPORT_WINDOW_DAYS (30), which
    #: keeps a report about what is happening now rather than about the last
    #: seven months averaged together.
    days: int | None = None


class IssueMapRequest(BaseModel):
    principal: str          # the person/organisation (e.g. "William Ruto")
    issue: str              # the issue/institution (e.g. "forestry", "SHA", "KRA")
    days: int = 365         # look-back window
    desired_outcome: str | None = None   # INPUT 2 of the issue framework; optional by design


_PERSON_SUBJECT_TYPES = {"person", "politician", "individual"}
_VALID_SUBJECT_TYPES = _PERSON_SUBJECT_TYPES | {"organization", "ministry", "business"}


def _ensure_politician(db, name: str, subject_type: str = "politician") -> Politician:
    subject_type = subject_type if subject_type in _VALID_SUBJECT_TYPES else "politician"
    politician = db.query(Politician).filter_by(name=name).first()
    if politician:
        # Let a caller correct/upgrade the subject type on an existing record.
        if subject_type != "politician" and getattr(politician, "subject_type", None) != subject_type:
            politician.subject_type = subject_type
            db.commit()
        return politician

    # Person-like subjects get honorific aliases; institutions do not.
    if subject_type in _PERSON_SUBJECT_TYPES:
        last_word = name.strip().split()[-1]
        aliases = [name, f"Hon. {name}", f"Sen. {last_word}", f"Honorable {name}"]
    else:
        aliases = [name]
    politician = Politician(name=name, aliases=aliases, keywords=[], subject_type=subject_type)
    db.add(politician)
    db.commit()
    return politician


#: How many source items the evidence section shows. Half of it is
#: reserved for the primary record, which has no engagement to compete
#: with and would otherwise never appear.
MENTION_SAMPLE = 10

def _extract_url(raw_payload: dict) -> str | None:
    """Best-effort link extraction — SocialCrawl's URL field location varies by
    surface (flat on brand-mentions, nested under `post` on TikTok/YouTube)."""
    if not isinstance(raw_payload, dict):
        return None
    fields = raw_payload.get("post") if isinstance(raw_payload.get("post"), dict) else raw_payload
    for key in ("url", "page_url", "link", "permalink"):
        value = fields.get(key) or raw_payload.get(key)
        if isinstance(value, str) and value:
            return value
        # Some feeds (RSS/Atom/GDELT) nest the link as {"href": ...} or a list
        # of such dicts. Never return the dict itself — it's unhashable and
        # blows up set/dedupe logic downstream.
        if isinstance(value, dict):
            href = value.get("href") or value.get("url")
            if isinstance(href, str) and href:
                return href
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item:
                    return item
                if isinstance(item, dict):
                    href = item.get("href") or item.get("url")
                    if isinstance(href, str) and href:
                        return href
    return None


def _freshness(politician_id: str) -> dict:
    """How current the underlying data is — the honest 'live vs stale' signal."""
    from sqlalchemy import func

    db = SessionLocal()
    try:
        from engine.db.period import dated

        # Dated items only: an undated item carries the collection time as a
        # stand-in, and counting it made every run look like a burst of news.
        newest = db.query(func.max(RawMention.posted_at)).filter(
            RawMention.politician_id == politician_id, dated()
        ).scalar()
        last_24h = db.query(func.count(RawMention.id)).filter(
            RawMention.politician_id == politician_id,
            RawMention.posted_at >= datetime.utcnow() - timedelta(hours=24),
            dated(),
        ).scalar()
        return {
            "newestMentionAt": newest.isoformat() if newest else None,
            "mentionsLast24h": int(last_24h or 0),
        }
    finally:
        db.close()


def _build_frontend_payload(politician: Politician, report) -> dict:
    """Shape a payload for the page — including a HALF-BUILT one.

    This is called on every section a run publishes, so it is handed the
    payload as it grows. Bracket access on keys that have not arrived yet
    raised KeyError, `_publish_partial` swallowed it and returned, and NOTHING
    was saved — so a reader watching a live run saw the stage text advance
    while no section ever appeared. Every read here is defensive for that
    reason: a missing key means "not yet", never an error.
    """
    payload = report.payload or {}
    sentiment = payload.get("sentiment_breakdown") or {}
    volume = payload.get("volume_trends") or {}

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
                # `headline` is the historical key; `text` and `author` are what
                # the UI actually reads. It read neither, so "Representative
                # mentions" rendered six rows of a platform chip, a bare "@" and
                # nothing else — the evidence section, empty, on every report.
                "headline": (mention.text or "")[:140],
                "text": (mention.text or "")[:400],
                "author": mention.author_handle,
                "posted_at": mention.posted_at.isoformat() if mention.posted_at else None,
                "url": url,
                # What KIND of document this is, so the page can say so. A
                # Gazette notice and a tweet are both "a mention" to this
                # loop and are not the same kind of evidence to a reader.
                "source_type": mention.source_type,
                "record": (mention.raw_payload or {}).get("record"),
                "_score": score,
                "_primary": (mention.raw_payload or {}).get("record") == "primary",
            }
        )
    scored_mentions.sort(key=lambda m: m["_score"], reverse=True)

    # Engagement ranks popularity. This product sells evidence.
    #
    # A Gazette notice has no likes, no views and no shares, because the
    # state does not post for engagement — so sorting on engagement alone
    # put the most citable document in the corpus last and cut it from the
    # top ten. The primary record was collected, stored, and then
    # structurally excluded from the one section a client opens to check
    # the work.
    #
    # Not fixed by inventing a score for it: a fabricated engagement figure
    # is the kind of number this codebase removes. Fixed by reserving room,
    # the same way `learning.py` reserves a share of every budget for
    # questions never asked — a guaranteed slot cannot be out-competed by
    # the category that is always plentiful.
    primary = [m for m in scored_mentions if m["_primary"]]
    rest = [m for m in scored_mentions if not m["_primary"]]
    reserved = min(len(primary), MENTION_SAMPLE // 2)
    chosen = primary[:reserved] + rest[:MENTION_SAMPLE - reserved]
    # Primary record first within the sample: a reader scanning it should
    # meet the state's own document before anyone's commentary on it.
    chosen.sort(key=lambda m: (m["_primary"], m["_score"]), reverse=True)
    top_mentions = [{k: v for k, v in m.items() if not k.startswith("_")}
                    for m in chosen]

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

    by_platform = sorted((volume.get("by_platform") or {}).items(), key=lambda kv: kv[1], reverse=True)
    total_volume = volume.get("total_mentions") or 1
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
            # Impact as the client defines it: coverage plus engagement, each
            # as a share of the run's own total. `score` is a weighted sum of
            # raw counts and is not comparable between two subjects, two
            # windows or even two accounts — so it cannot be used to rank
            # anything for a reader.
            "impact": item.get("impact"),
            "band": item.get("impact_band"),
            "coverage_share": item.get("coverage_share"),
            "engagement_share": item.get("engagement_share"),
        }
        for i, item in enumerate((payload.get("influence_summary") or [])[:10])
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
        for item in (payload.get("influence_summary") or [])[:8]:
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
        "generatedAt": datetime.utcnow().isoformat(),
        # Which backend produced this. A run served by a stand-in model proves
        # the pipeline works, not that the analysis is sound — so it is labelled
        # in the payload and on screen rather than left to look like the real
        # thing. See engine/llm.py::is_test_grade.
        "grade": _llm.report_grade(),
        "freshness": _freshness(politician.id),
        "summary": payload.get("executive_summary") or "",
        "sentiment": {
            "negative": sentiment.get("negative_pct"),
            "neutral": sentiment.get("neutral_pct"),
            "positive": sentiment.get("positive_pct"),
            "totalAnalyzed": sentiment.get("total_mentions_analyzed"),
        },
        "volume": {
            "total": volume.get("total_mentions"),
            "platforms": len(volume.get("by_platform") or {}),
            "last72h": sum(
                c for d, c in (volume.get("by_day") or {}).items()
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
                # Withheld where they carry nothing the mention count does
                # not. Engagement is zero across a news corpus, so `strength`
                # IS the mention count there, and a storyline that began
                # inside the window has no growth rate to report. Handed all
                # three, a model wrote "(69 mentions, strength 69, strong
                # growth 69%)" into an executive brief — one number wearing
                # three hats, each read by a client as separate corroboration.
                #
                # Absent rather than zero: a zero would be read as "flat",
                # which is a measurement nobody made.
                **({"strength": n["strength_score"]}
                   if n.get("strength_basis") == "mentions_and_engagement" else {}),
                **({"growth": n["growth_rate"]}
                   if n.get("growth_basis") == "measured" else {}),
                "growth_basis": n.get("growth_basis"),
                "mentions": n["mention_count"],
                "description": n["description"],
                "evidence": n.get("evidence", []),
                "labelled_by": n.get("labelled_by"),
            }
            for n in (payload.get("narrative_breakdown") or [])
        ],
        "risks": payload.get("risks", []),
        "opportunities": payload.get("opportunities", []),
        "trends": payload.get("trends", []),
        # Corpus-grounded deep sections (empty when generated by older reports).
        "executiveBrief": payload.get("executive_brief", ""),
        # The resolved citations for that prose. Without them the page has a
        # "[1]" it cannot turn back into a link.
        "executiveBriefCitations": payload.get("executive_brief_citations", []),
        "summaryCitations": payload.get("executive_summary_citations", []),
        "publicVoice": payload.get("public_voice", {}),
        "platformPulse": payload.get("platform_pulse", []),
        "timeline": payload.get("timeline", []),
        "influencerStances": payload.get("influencer_stances", []),
        "narrativeDeepDives": payload.get("narrative_deep_dives", []),
        "deepInsights": payload.get("deep_insights", {}),
        "coverage": payload.get("coverage", {}),
        "topComments": top_comments,
        "recency": {
            "last7Days": volume.get("last_7_days", 0),
            "last30Days": volume.get("last_30_days", 0),
        },
        "dataCoverage": payload.get("data_coverage", {}),
        "sinceLastReport": payload.get("since_last_report"),
        "sentimentHistory": payload.get("sentiment_history", []),
        # The audit trail and the client deliverable. The frontend has read
        # these since the weekly dashboard was built — the API just never sent
        # them, so on a live report the Sentiment Framework tab never appeared
        # and the Status section was permanently blank. snake_case here because
        # that is the name the renderers already use.
        "sentiment_framework": payload.get("sentiment_framework"),
        "runHealth": payload.get("run_health"),
        # The stages that run after delivery: pending until the claim audit
        # and resolution have finished, then done with their timings.
        "backOffice": payload.get("back_office"),
        # Which sections were counted rather than read. Without this the page
        # cannot tell the reader which half of the report a model wrote.
        "derivedSections": payload.get("derived_sections") or [],
        # An empty corpus is a finding, not a blank page.
        "nothingCollected": payload.get("nothing_collected"),
        # The run died partway and these are the sections that finished. A
        # reader must never mistake an incomplete report for a complete one.
        "partialFailure": payload.get("partial_failure"),
        "sectionStatus": payload.get("section_status"),
        # How much of the corpus the sentiment reading actually rests on, and
        # why anything was lost. Without it, "0 scored" and "genuinely neutral"
        # are the same report.
        "sentimentScoring": payload.get("sentiment_scoring", {}),
        # Period-over-period series behind the client's dashboard charts.
        "periodSeries": payload.get("period_series", {}),
        # The worklist: what to act on, what it is about, and whether the
        # thing it addresses has moved since the last report. The dashboard
        # has read this key since it was built; nothing wrote it until now.
        "actions": payload.get("actions"),
        "verification": payload.get("verification", {}),
        "evidence_gate": payload.get("evidence_gate", {}),
        # Every factual claim in the prose, with the sources that back it and
        # the passage each one turns on. Written on every run since the
        # verification agent shipped, and never shown to anyone.
        "claims": payload.get("claims", []),
        # The investigator's agenda: what the file still can't answer, and the
        # queries the next run should ask. Each item carries `question`, which
        # is the shape the dashboard's Key Questions renderer already expects.
        "open_questions": (payload.get("investigation") or {}).get("agenda", []),
        "investigation_leads": (payload.get("investigation") or {}).get("follow_up_queries", []),
        # Names in the report that appear nowhere in the sources. Shipped to
        # the page because a reader has no other way to tell a grounded
        # report from an inventive one, and a client reading an invented name
        # is the fastest way to lose them.
        "grounding": payload.get("grounding"),
        # How good this report is, measured: full text versus headlines,
        # sentences a source backs, figures that match nothing, citations
        # that point at nothing. Operator view only — see reports/quality.py.
        "quality": payload.get("quality"),
    }


# Report generation (LLM calls, entity-linking, sentiment) easily exceeds the
# request timeout of a free-tier hosting platform's load balancer, which kills
# the connection mid-flight and surfaces as a generic "Failed to fetch" in the
# browser. Run it in a background thread and have the frontend poll instead
# of holding one long-lived request open.
_jobs: dict[str, dict] = {}

# There were pre-generated reports here — a July report for two names —
# served instead of the live run when that run failed, marked a success.
# A months-old report about another period, shown in place of this one, is
# the fake data this product exists not to sell, and it pre-empted
# `_partial_payload`, which hands over what the failed run DID produce.
# Removed 22 Oct 2026; see test_no_stored_report_stands_in_for_a_live_one.py.


# ---------------------------------------------------------------------------
# Durable run progress
#
# Streaming sections into `_jobs` alone still tied the result to one browser
# sitting on one page: a run that outlived the poll window, a reload, a closed
# tab or a restarted instance threw away everything already produced. The work
# had happened — there was nowhere to look for it.
#
# Every section is therefore also written to `run_progress`, keyed by subject
# rather than by a job id that only means something to the process that minted
# it. A reader can then pick the run up from anywhere, at any time, including
# after the process that started it is gone.
# ---------------------------------------------------------------------------

def _subject_key(name: str, issue: str | None = None) -> str:
    base = (name or "").strip().lower()
    return f"{base}|{issue.strip().lower()}" if issue else base


def _save_progress(subject_key: str, kind: str, *, job_id: str | None = None,
                   status: str | None = None, stage: str | None = None,
                   sections_ready: list | None = None, payload: dict | None = None,
                   error: str | None = None) -> None:
    """Upsert one run's progress. Never raises — progress is a convenience and
    must not be able to cost the run it is describing."""
    # Every stage and every section lands here, which makes this the run's
    # heartbeat. `_inflight_job` uses it to tell a slow run from a dead one.
    if job_id:
        job = _jobs.get(job_id)
        if job is not None:
            job["last_update"] = time.time()
    from engine.db.models import RunProgress

    db = None
    try:
        db = SessionLocal()
        row = db.query(RunProgress).filter_by(subject_key=subject_key, kind=kind).first()
        if row is None:
            row = RunProgress(subject_key=subject_key, kind=kind, started_at=datetime.utcnow())
            db.add(row)
        if job_id is not None:
            row.job_id = job_id
        if status is not None:
            row.status = status
        if stage is not None:
            row.stage = stage
        if sections_ready is not None:
            row.sections_ready = list(sections_ready)
        if payload is not None:
            row.payload = _json_safe(payload)
        # An error is recorded, but a later successful run clears it rather
        # than leaving a stale failure attached to a good result.
        row.error = error
        row.updated_at = datetime.utcnow()
        db.commit()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _read_progress(subject_key: str, kind: str) -> dict | None:
    from engine.db.models import RunProgress

    db = None
    try:
        db = SessionLocal()
        row = db.query(RunProgress).filter_by(subject_key=subject_key, kind=kind).first()
        if row is None:
            return None
        # A stored row still saying "running" hours after the process that
        # wrote it died leaves a reader watching a spinner that will never
        # resolve — and the row outlives the container, so a restart does not
        # clear it. Report it for what it is. The already-collected payload is
        # still returned: the work it did reach is not lost.
        status = row.status
        if status == "running" and row.updated_at:
            from datetime import datetime as _dt

            silent = (_dt.utcnow() - row.updated_at).total_seconds()
            if silent > _JOB_SILENCE_SECONDS:
                status = "stalled"
        return {
            "status": status,
            "job_id": row.job_id,
            "stage": row.stage,
            "sections_ready": row.sections_ready or [],
            "payload": row.payload or {},
            "error": row.error,
            "started_at": str(row.started_at) if row.started_at else None,
            "updated_at": str(row.updated_at) if row.updated_at else None,
        }
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


@app.get("/api/progress")
def get_progress(name: str, kind: str = "report", issue: str | None = None,
                 x_api_key: str | None = Header(default=None)):
    """A run's state by SUBJECT, not by job id.

    This is what makes a run survive the page that started it. A job id only
    means something to the process that minted it; a subject is something the
    client always knows and can ask about again tomorrow.
    """
    _require_api_key(x_api_key)
    progress = _read_progress(_subject_key(name, issue), kind)
    if progress is None:
        raise HTTPException(status_code=404, detail="no run recorded for that subject")
    return {"ok": True, **progress}


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", (text or "subject").lower()).strip("-") or "subject"


@app.get("/api/report/download")
def download_report_pdf(name: str, x_api_key: str | None = Header(default=None)):
    """A downloadable PDF of the stored report — no link, no login, a file
    that can be emailed or sent straight to someone outside this app.

    Renders through the exact same code the browser uses (window.ZENITH.
    renderReport), so a PDF can never show something the live page wouldn't.
    """
    _require_api_key(x_api_key)
    from engine.reports import pdf_export

    if not pdf_export.chromium_available():
        raise HTTPException(status_code=503,
                            detail="PDF export is not available in this environment")
    progress = _read_progress(_subject_key(name), "report")
    if not progress or not progress.get("payload"):
        raise HTTPException(status_code=404, detail="no stored report for that subject")

    pdf_bytes = pdf_export.render_payload_to_pdf(
        render_frontend_document(), "report", progress["payload"])
    filename = f"{_slug(name)}-report.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/issue-map/download")
def download_issue_map_pdf(principal: str, issue: str,
                           x_api_key: str | None = Header(default=None)):
    """Same as /api/report/download, for a stored issue map."""
    _require_api_key(x_api_key)
    from engine.reports import pdf_export

    if not pdf_export.chromium_available():
        raise HTTPException(status_code=503,
                            detail="PDF export is not available in this environment")
    progress = _read_progress(_subject_key(principal, issue), "issue_map")
    if not progress or not progress.get("payload"):
        raise HTTPException(status_code=404, detail="no stored issue map for that pair")

    pdf_bytes = pdf_export.render_payload_to_pdf(
        render_frontend_document(), "issue_map", progress["payload"])
    filename = f"{_slug(principal)}-x-{_slug(issue)}-issue-map.pdf"
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


class _PartialReport:
    """Enough of an IntelligenceReport for `_build_frontend_payload` to shape a
    run that is still in progress. The real row does not exist until the
    pipeline commits it."""

    def __init__(self, payload: dict, window_start: datetime, window_end: datetime):
        self.payload = payload
        self.window_start = window_start
        self.window_end = window_end


# Shaping a partial costs two small queries, and the base statistics land as a
# burst of a dozen keys at once. Rebuilding for each of them would be a dozen
# round-trips describing identical data, so bursts collapse into one rebuild.
# Nothing is lost: the accumulating payload keeps every key, and the next
# section (minutes later, on a real run) publishes all of it.
_PARTIAL_MIN_INTERVAL_SECONDS = 1.0

#: Sections that always reach the page at once: the client's deliverable, and
#: the ledger the pipeline publishes immediately before each long phase — so
#: whatever the throttle held back is flushed before minutes of silence.
_FLUSH_ON_SECTIONS = frozenset({"sentiment_framework", "section_status"})


def _has_real_content(value) -> bool:
    """True when this is genuine content, not the empty/placeholder shape a
    preview publishes to keep the payload's structure stable while the real
    number isn't known yet.

    `corpus_preview()` publishes `sentiment_breakdown` early as
    `{"positive_pct": None, "neutral_pct": None, "negative_pct": None,
    "total_mentions_analyzed": 0}` — a non-empty dict, so the old check
    (`v not in (None, [], {}, "")`) marked it "ready" the instant it was
    published. The checklist then showed a green check and "finished and
    safe to read" next to a card whose every number was still "—", for as
    long as the real analysis took to run — seconds when the model answered
    fast, minutes once it didn't. A dict (or list) counts as real content
    only if something inside it is non-empty; a scalar counts if it isn't
    itself None/[]/{}/"" .
    """
    if isinstance(value, dict):
        return any(_has_real_content(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_real_content(v) for v in value)
    # Plain Python truthiness, not a specific-placeholder check: the preview's
    # `total_mentions_analyzed: 0` and `positive_pct: None` are both falsy, and
    # so is every other "not yet known" value this codebase uses (documented
    # explicitly in generator.py: "None, not 0.0, when nothing was scored").
    return bool(value)


def _publish_partial(job_id: str, politician, payload: dict,
                     window_start: datetime, window_end: datetime) -> None:
    """Shape whatever the pipeline has produced so far into the frontend's
    payload and hang it on the job, so a reader can start on section one while
    section eight is still being written.

    Best-effort by design: a failure here must never touch the run itself. It
    is, however, RECORDED — this returning silently is why a run could advance
    its stage text for an hour while not one section reached the page.

    It no longer waits for `sentiment_breakdown` and `volume_trends` before
    publishing anything. That gate existed because the shaper could not survive
    a half-built payload; now that it can, holding every section hostage to two
    particular keys only delays the first thing a reader sees.
    """
    job = _jobs.get(job_id)
    if job is None or job.get("status") != "running":
        return
    if not payload:
        return
    now = time.time()
    if now - job.get("partial_at", 0.0) < _PARTIAL_MIN_INTERVAL_SECONDS:
        return
    job["partial_at"] = now
    try:
        shaped = _build_frontend_payload(
            politician, _PartialReport(dict(payload), window_start, window_end)
        )
    except Exception as exc:  # noqa: BLE001 — must never cost the run
        # Recorded, NOT swallowed. This returning silently is why a live run
        # could advance its stage text for an hour while not one section ever
        # reached the page: every publish raised, every raise was discarded,
        # and nothing anywhere said so.
        stages_mod.current().failed("publish_partial", exc)
        return
    ready = sorted(k for k, v in payload.items() if _has_real_content(v))
    job["partial"] = shaped
    job["sections_ready"] = ready
    # And durably, keyed by subject, so the run outlives this process and this
    # browser tab.
    _save_progress(_subject_key(politician.name), "report", job_id=job_id,
                   status="running", stage=job.get("stage"),
                   sections_ready=ready, payload=shaped)


def _set_stage(job_id: str, subject_key: str, kind: str, text: str) -> None:
    """Record what a run is doing, in memory AND durably.

    The stage used to be written to the job dict only. Everything before the
    first section lands — ingestion, entity linking, people extraction,
    whole-corpus sentiment — is the longest part of a run, and for all of it a
    reader who came back was told "Starting…" however far along it actually
    was. Storing it means the answer to "is this thing moving?" survives the
    page, the poll window and the process.
    """
    job = _jobs.get(job_id)
    if job is not None:
        job["stage"] = text
    _save_progress(subject_key, kind, job_id=job_id, status="running", stage=text)


def _partial_payload(name: str) -> dict | None:
    """Whatever this subject's run had already produced before it failed.

    Sections are written to the progress store the moment each one lands —
    that is what lets a reader watch a report assemble — so after a failure
    the finished ones are still there.

    Returns None when there is genuinely almost nothing, because "the run
    failed and produced nothing" and "the run failed after producing most of
    a report" are different outcomes and must not render the same.
    """
    try:
        progress = _read_progress(_subject_key(name), "report")
    except Exception:  # noqa: BLE001 — a recovery path must never raise
        return None
    payload = (progress or {}).get("payload")
    if not isinstance(payload, dict):
        return None
    real = {k: v for k, v in payload.items()
            if v not in (None, [], {}, "") and not k.startswith("_")}
    return payload if len(real) >= 3 else None


def _run_report_job(job_id: str, name: str, subject_type: str = "politician",
                    window_days: int | None = None) -> None:
    # Claim the subject's progress row BEFORE any work starts, clearing whatever
    # the last run left there.
    #
    # Without this, a failure recorded by an earlier run outlives it: the next
    # run takes longer than the poll window, the page falls back to stored
    # progress, and shows that old error as though it had just happened — with
    # its old stack trace, from a build that is no longer deployed. A fixed bug
    # then looks unfixed, and the fix gets debugged instead of the run.
    subject_key = _subject_key(name)
    # Clamp: the default is a month of recent coverage, and a caller may widen
    # it, but not without limit — reading years of corpus is how a run stops
    # being a monitoring product.
    window_days = max(1, min(int(window_days or settings.report_window_days),
                             settings.report_window_max_days))
    _save_progress(subject_key, "report", job_id=job_id,
                   status="running", stage="Starting…", error=None)
    from sqlalchemy.exc import DBAPIError

    from engine.db.session import engine as _db_engine

    def _fail(exc: Exception) -> None:
        # Surface a compact diagnostic to the operator's own UI (API-key-gated).
        # Never a stored report in place of this one: a failure is said, and
        # what the run did produce is delivered under a banner (below).
        traceback.print_exc()
        # A dead model is an operator instruction, not a stack trace. Preflight
        # already turned the provider's error into the specific thing to change,
        # so say that instead of a file and line number nobody can act on.
        if isinstance(exc, health_mod.PreflightFailed):
            diag = f"{exc} — {exc.remedy} (provider said: {exc.error})"[:800]
            _save_progress(_subject_key(name), "report", job_id=job_id,
                           status="failed", error=diag)
            _jobs[job_id] = {"status": "done", "ok": False, "error": diag,
                             "preflight": exc.to_dict(), "created_at": time.time()}
            return

        tb = traceback.format_exc()
        frames = [ln.strip() for ln in tb.splitlines()
                  if ln.strip().startswith('File "') and "/engine/" in ln]
        where = (" @ " + frames[-1].split("/engine/")[-1]) if frames else ""
        diag = f"{type(exc).__name__}: {exc}{where}".strip()[:500]

        # Deliver what the run DID produce.
        #
        # Sections are published and saved as each one lands, so a run that
        # collected a corpus, digested it and finished eleven analysts before
        # dying on a database query has eleven sections sitting in the store.
        # Throwing them away and showing a stack trace is the worst outcome
        # available: the work was done, the model calls were paid for, the
        # reader waited seven minutes — and this hands them nothing.
        #
        # The product's founding rule, at the scale of a whole run. A run
        # that failed must not look like a run that found nothing; it must
        # not look like a run that never happened either. So: the sections,
        # with the failure stated above them.
        partial = _partial_payload(name)
        if partial is not None:
            partial = {**partial, "partial_failure": {
                "error": diag,
                "note": ("This report is incomplete. The run failed partway "
                         "through and these are the sections that had already "
                         "finished. Nothing below was invented to fill the gap "
                         "— the sections that are missing are missing."),
            }}
            _save_progress(_subject_key(name), "report", job_id=job_id,
                           status="failed",
                           error=f"report generation failed: {diag}",
                           payload=partial)
            _jobs[job_id] = {"status": "done", "ok": True, "report": partial,
                             "partial": True, "error_detail": diag,
                             "created_at": time.time()}
            return

        _save_progress(_subject_key(name), "report", job_id=job_id,
                       status="failed", error=f"report generation failed: {diag}")
        _jobs[job_id] = {"status": "done", "ok": False,
                         "error": f"report generation failed: {diag}",
                         "created_at": time.time()}

    # Serverless Postgres (Neon) can drop the connection when its compute
    # suspends, so the query fails. The pipeline is incremental (stored mentions
    # + link_checked/have_sentiment guards), so retrying with a fresh connection
    # resumes from progress instead of redoing work.
    #
    # The back office is collected here and run only after the loop — after
    # the reader has the report and after this session is closed.
    back_office: list = []
    delivered_name = name
    for attempt in range(3):
        db = SessionLocal()
        try:
            politician = _ensure_politician(db, name, subject_type)
            window_end = datetime.utcnow()
            window_start = window_end - timedelta(days=window_days)
            _set_stage(job_id, subject_key, "report",
                       f"Scanning social platforms and news for “{name}”…")
            from engine.pipeline import run_analysis, run_ingestion

            live_payload: dict = {}

            def _on_section(key, value):
                live_payload[key] = value
                if key in _FLUSH_ON_SECTIONS:
                    # Never throttled. The throttle DROPS a publish that lands
                    # inside its interval rather than deferring it, so the
                    # burst of sections just before a long silent phase — the
                    # Sentiment Framework among them — sat unseen until the
                    # first analyst came back minutes later.
                    _job = _jobs.get(job_id)
                    if _job is not None:
                        _job["partial_at"] = 0.0
                _publish_partial(job_id, politician, live_payload, window_start, window_end)

            ingestion_run = run_ingestion(db, politician, window_start, window_end, credit_budget=300.0)
            stats = (ingestion_run.stats or {}) if ingestion_run else {}
            found = stats.get("mentions_total")
            _set_stage(job_id, subject_key, "report", (
                f"Read {found} new mentions. Now scoring sentiment, narratives, voices and "
                "network across the whole stored corpus — this is the long part, and the "
                "first section lands when it finishes."
                if found
                else "Scoring sentiment, narratives, voices and network across the stored "
                     "corpus — this is the long part, and the first section lands when it "
                     "finishes."
            ))
            # Collected, not run: resolution, the knowledge graph and the
            # claim audit wait until the reader has the report. See
            # `pipeline.run_back_office`.
            back_office.clear()
            report = run_analysis(db, politician, "live-demo", window_start, window_end,
                                  ingestion_run=ingestion_run, on_section=_on_section,
                                  defer_back_office=back_office)
            finished = _build_frontend_payload(politician, report)
            _save_progress(_subject_key(politician.name), "report", job_id=job_id,
                           status="done", stage="Complete.",
                           sections_ready=sorted(report.payload or {}), payload=finished)
            _jobs[job_id] = {
                "status": "done", "ok": True,
                "report": finished,
                "created_at": time.time(), "live": True,
            }
            delivered_name = politician.name
            break
        except DBAPIError as exc:
            if getattr(exc, "connection_invalidated", False) and attempt < 2:
                traceback.print_exc()
                try:
                    db.rollback()
                except Exception:
                    pass
                _db_engine.dispose()  # drop stale pooled connections, force fresh ones
                _set_stage(job_id, subject_key, "report", "Reconnecting to the database…")
                time.sleep(3 * (attempt + 1))
                continue
            _fail(exc)
            return
        except Exception as exc:  # noqa: BLE001 - never hard-fail the UI
            _fail(exc)
            return
        finally:
            try:
                db.close()
            except Exception:
                pass

    # The reader has the report. Now the work that serves the platform.
    for task in back_office:
        _back_office_after_delivery(job_id, delivered_name, task)


#: One back office per subject at a time. Two would write the same entities
#: and events; the second waits, and gives up rather than queue for ever.
_BACK_OFFICE_LOCKS: dict[str, threading.Lock] = {}
_BACK_OFFICE_LOCKS_GUARD = threading.Lock()


def _back_office_after_delivery(job_id: str, name: str, task) -> None:
    """Run a delivered report's back office, then fold its results in.

    Never raises and never flips a delivered report back to "running": the
    reader already has a finished report, and nothing here may take it away.
    The enriched version replaces it in the job and the progress store only
    if no newer run for this subject has claimed them in the meantime.
    """
    from engine.db.models import IntelligenceReport
    from engine.pipeline import run_back_office

    key = _subject_key(name)
    with _BACK_OFFICE_LOCKS_GUARD:
        lock = _BACK_OFFICE_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(timeout=settings.back_office_deadline_seconds):
        task.ledger.failed("back_office", "an earlier report's back office for this "
                           "subject was still running, so this one was not started")
        return
    try:
        run_back_office(task)
    except Exception:  # noqa: BLE001 — after delivery, a failure costs nothing visible
        traceback.print_exc()
        return
    finally:
        lock.release()

    try:
        db = SessionLocal()
        try:
            report = db.get(IntelligenceReport, task.report_id)
            politician = db.get(Politician, task.politician_id)
            if report is None or politician is None:
                return
            enriched = _build_frontend_payload(politician, report)
            ready = sorted(report.payload or {})
        finally:
            db.close()
        job = _jobs.get(job_id)
        if job is not None and job.get("status") == "done" and job.get("ok"):
            job["report"] = enriched
        progress = _read_progress(key, "report") or {}
        if progress.get("job_id") in (None, job_id) and progress.get("status") != "running":
            _save_progress(key, "report", job_id=job_id, status="done",
                           stage="Complete.", sections_ready=ready, payload=enriched)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


@app.post("/api/report")
def generate_report(req: ReportRequest, request: Request, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    _check_rate_limit(request.client.host if request.client else "unknown")
    _evict_stale_jobs()

    name = req.name.strip()

    # Re-attach to a run already in flight for this subject rather than starting
    # a second one. A long run invites the user to click Generate again; doing so
    # used to launch a competing pipeline over the same rate-limited API and the
    # same rows, so both ran slower than one would have and the wait that
    # prompted the click got worse.
    existing = _inflight_job(name, req.type)
    if existing:
        return {"ok": True, "job_id": existing, "already_running": True}

    job_id = uuid.uuid4().hex
    # Always run the LIVE pipeline so every report reflects the newest data.
    # A pre-generated report (if any) is used only as a fallback on failure
    # (see _run_report_job) — never as a substitute for a fresh run.
    _jobs[job_id] = {"status": "running", "created_at": time.time(),
                     "subject": (name.lower(), req.type)}
    thread = threading.Thread(target=_run_report_job,
                              args=(job_id, name, req.type, req.days), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def _inflight_job(name: str, report_type: str) -> str | None:
    """The id of a still-running job for this subject, if there is one.

    A job that has gone silent past `_JOB_SILENCE_SECONDS` is presumed dead
    and marked as such, so the caller starts a fresh run instead of being
    reconnected to something that stopped producing hours ago.
    """
    key = (name.strip().lower(), report_type)
    now = time.time()
    for job_id, job in list(_jobs.items()):
        if job.get("status") != "running" or job.get("subject") != key:
            continue
        silent_for = now - (job.get("last_update") or job.get("created_at") or now)
        if silent_for > _JOB_SILENCE_SECONDS:
            job["status"] = "done"
            job["ok"] = False
            job["error"] = (
                f"This run stopped producing {int(silent_for // 60)} minutes ago and was "
                "abandoned so a new one could start. Nothing was lost — whatever it had "
                "already stored is still there and the next run builds on it."
            )
            continue
        return job_id
    return None


@app.get("/api/report/{job_id}")
def get_report(job_id: str, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return job


def _run_issue_map_job(job_id: str, principal: str, issue: str, days: int,
                       desired_outcome: str | None = None) -> None:
    from datetime import datetime, timedelta

    from engine.reports.issue_map import build_issue_map

    _save_progress(_subject_key(principal, issue), "issue_map", job_id=job_id,
                   status="running", stage="Starting…", error=None)
    try:
        we = datetime.utcnow()
        ws = we - timedelta(days=max(1, days))

        # Same contract as a report run: publish each stage as it lands so the
        # reader watches the map assemble instead of a spinner. An issue map on
        # the real acquisition stack is a long run — that is the point of it.
        def _on_section(key, value):
            job = _jobs.get(job_id)
            if job is None or job.get("status") != "running":
                return
            if key == "stage":
                job["stage"] = value
                _save_progress(_subject_key(principal, issue), "issue_map",
                               job_id=job_id, status="running", stage=value)
                return
            partial = job.setdefault("partial_issue_map", {
                "principal": principal, "issue": issue,
                "window": {"start": ws, "end": we},
            })
            partial[key] = value
            _save_progress(_subject_key(principal, issue), "issue_map", job_id=job_id,
                           status="running", stage=job.get("stage"),
                           sections_ready=sorted(partial), payload=partial)

        payload = build_issue_map(principal, issue, window_start=ws, window_end=we,
                                  desired_outcome=desired_outcome, on_section=_on_section)
        # Store before publishing. `_jobs` lives in this process only, and a
        # free instance restarts; without this, an hour of scraping and four
        # analysts are simply gone when it does, and the client's poll 404s
        # with nothing to fall back on.
        _store_issue_map(principal, issue, payload)
        _save_progress(_subject_key(principal, issue), "issue_map", job_id=job_id,
                       status="done", stage="Complete.",
                       sections_ready=sorted(payload), payload=payload)
        _jobs[job_id] = {"status": "done", "ok": True, "issue_map": payload, "created_at": time.time()}
    except Exception as exc:  # surface server-side; this is an API-key-gated operator tool
        traceback.print_exc()
        diag = f"{type(exc).__name__}: {exc}".strip()[:400]
        # Record the failure against the subject too — a reader who comes back
        # later deserves the reason, not an empty screen.
        _save_progress(_subject_key(principal, issue), "issue_map", job_id=job_id,
                       status="failed", error=f"issue map failed: {diag}")
        _jobs[job_id] = {"status": "done", "ok": False, "error": f"issue map failed: {diag}",
                         "created_at": time.time()}


@app.post("/api/issue-map")
def create_issue_map(req: IssueMapRequest, request: Request, x_api_key: str | None = Header(default=None)):
    """Map the intersection of a principal and an issue/institution — e.g.
    'William Ruto' × 'forestry'. Runs async like /api/report; poll
    /api/report/{job_id} for the result."""
    _require_api_key(x_api_key)
    _check_rate_limit(request.client.host if request.client else "unknown")
    _evict_stale_jobs()

    principal = req.principal.strip()
    issue = req.issue.strip()
    if not principal or not issue:
        raise HTTPException(status_code=400, detail="principal and issue are both required")
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {"status": "running", "created_at": time.time()}
    thread = threading.Thread(target=_run_issue_map_job, args=(job_id, principal, issue, req.days, (req.desired_outcome or "").strip() or None), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def _issue_period(issue: str) -> str:
    """How an issue map is filed in `intelligence_reports`.

    Reusing that table rather than adding one keeps issue maps in the same
    place as reports for the same subject — they are both analyses of that
    subject — and needs no migration.
    """
    return f"issue:{issue.strip().lower()}"


def _json_safe(value):
    """Coerce a payload to something JSONB will accept.

    An issue map carries real `datetime` objects — the window, the generated-at
    stamp, every `posted_at` in the evidence sample — and psycopg2 refuses
    them. Without this the store fails, the exception is swallowed by the
    guard below, and recovery silently never works: exactly the kind of
    failure that looks like a feature nobody uses.
    """
    return json.loads(json.dumps(value, default=str))


def _store_issue_map(principal: str, issue: str, payload: dict) -> None:
    """Persist a finished issue map. Never raises: a storage failure must not
    cost the caller the map they are waiting for."""
    from engine.db.models import IntelligenceReport

    db = None
    try:
        db = SessionLocal()
        subject = db.query(Politician).filter_by(name=principal.strip()).first()
        if subject is None:
            return
        window = payload.get("window") or {}
        db.add(
            IntelligenceReport(
                politician_id=subject.id,
                period=_issue_period(issue),
                window_start=window.get("start") or datetime.utcnow(),
                window_end=window.get("end") or datetime.utcnow(),
                payload=_json_safe(payload),
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _lookup_issue_map(principal: str, issue: str) -> dict | None:
    """The most recent stored map for this pair, if there is one."""
    from engine.db.models import IntelligenceReport

    db = None
    try:
        db = SessionLocal()
        subject = db.query(Politician).filter_by(name=principal.strip()).first()
        if subject is None:
            return None
        row = (
            db.query(IntelligenceReport)
            .filter_by(politician_id=subject.id, period=_issue_period(issue))
            .order_by(IntelligenceReport.generated_at.desc())
            .first()
        )
        return row.payload if row else None
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


@app.get("/api/issue-map/latest")
def latest_issue_map(principal: str, issue: str, x_api_key: str | None = Header(default=None)):
    """Recover a stored map for principal x issue.

    A job id only means something to the process that created it, so when that
    process restarts mid-run the client is left holding an id that 404s. This
    is how it gets its answer back instead of an hour of work disappearing.
    """
    _require_api_key(x_api_key)
    payload = _lookup_issue_map(principal, issue)
    if payload is None:
        raise HTTPException(status_code=404, detail="no stored issue map for that pair")
    return {"ok": True, "issue_map": payload, "recovered": True}


# Parked or removed on 22 Oct 2026 (docs/FOUNDATION.md, "What to cut"):
# the Network graph and its edge receipts, the knowledge-graph traversal,
# compare, alerts, livefeed, simulate and tipline — none was called by the
# page — and the Baraza leaderboard, which listed the subjects being
# researched, with counts, to anyone, without a key. They are in git
# history; Network returns when the fact store can say who is connected to
# whom from evidence rather than from co-mentions.


_PROCESS_START = time.time()
_metrics_cache: dict = {"at": 0.0, "data": None}
_METRICS_TTL = 30

# All ingestion connectors and their config flag, for the source-health board.
_SOURCE_FLAGS = {
    "socialcrawl": "socialcrawl_api_key",
    "newsapi": "newsapi_key",
    "gdelt": "enable_gdelt",
    "wayback": "enable_wayback",
    "twscrape": "enable_twscrape",
    "agentreach": "enable_agentreach",
    "facebook": "enable_facebook_scraper",
}


def _admin_metrics() -> dict:
    from datetime import date, timedelta

    from sqlalchemy import func

    from engine.db.models import (
        Alert,
        Entity,
        IngestionRun,
        IntelligenceReport,
        LlmUsage,
        MentionSentiment,
        Politician,
        RawMention,
    )

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        # --- credits ---
        socialcrawl_balance = None
        if settings.socialcrawl_api_key:
            try:
                from engine.ingestion.socialcrawl_connector import SocialCrawlConnector

                socialcrawl_balance = SocialCrawlConnector().check_balance()
            except Exception:
                socialcrawl_balance = None

        def _spend(since_days: int) -> dict:
            cutoff = date.today() - timedelta(days=since_days)
            rows = db.query(
                func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                func.coalesce(func.sum(LlmUsage.calls), 0),
            ).filter(LlmUsage.day >= cutoff).one()
            in_tok, out_tok, calls = int(rows[0]), int(rows[1]), int(rows[2])
            # Price at the rates of the backend that actually served the calls.
            # Reporting an OpenRouter run at Anthropic's rates overstated it by
            # several times, which is worse than showing nothing to someone
            # working out whether they can afford another run.
            from engine import llm as _llm

            if _llm.provider() == "anthropic":
                price_in, price_out = settings.anthropic_price_in, settings.anthropic_price_out
            else:
                price_in, price_out = settings.llm_price_in, settings.llm_price_out
            cost = (in_tok / 1e6) * price_in + (out_tok / 1e6) * price_out
            return {"input_tokens": in_tok, "output_tokens": out_tok, "calls": calls,
                    "usd": round(cost, 4), "priced_at": {"in": price_in, "out": price_out}}

        credits = {
            "socialcrawl_balance": socialcrawl_balance,
            "anthropic_today": _spend(0),
            "anthropic_7d": _spend(7),
            "anthropic_30d": _spend(30),
            "anthropic_model": settings.anthropic_model,
        }

        # --- sources (enabled flag + latest run health) ---
        last_run = db.query(IngestionRun).order_by(IngestionRun.started_at.desc()).first()
        health = ((last_run.stats or {}).get("source_health") if last_run else {}) or {}
        sources = []
        for name, flag in _SOURCE_FLAGS.items():
            enabled = bool(getattr(settings, flag, ""))
            # health keys are platform names; match loosely by connector name.
            h = next((v for k, v in health.items() if name in k or k in name), None)
            status = "disabled"
            if enabled:
                if h is None:
                    status = "idle"
                elif h.get("succeeded", 0) == h.get("attempted", 0) and h.get("attempted", 0) > 0:
                    status = "ok"
                elif h.get("succeeded", 0) > 0:
                    status = "degraded"
                else:
                    status = "down"
            sources.append({
                "name": name, "enabled": enabled, "status": status,
                "results": (h or {}).get("results"), "failures": (h or {}).get("failures"),
            })

        # --- corpus ---
        def _mentions_since(hours: int) -> int:
            return db.query(func.count(RawMention.id)).filter(
                RawMention.posted_at >= now - timedelta(hours=hours)
            ).scalar()

        by_platform = dict(
            db.query(RawMention.platform, func.count(RawMention.id)).group_by(RawMention.platform).all()
        )
        by_source_type = dict(
            db.query(RawMention.source_type, func.count(RawMention.id)).group_by(RawMention.source_type).all()
        )
        sentiment_counts = dict(
            db.query(MentionSentiment.sentiment, func.count(MentionSentiment.mention_id))
            .group_by(MentionSentiment.sentiment).all()
        )
        corpus = {
            "politicians": db.query(func.count(Politician.id)).scalar(),
            "total_mentions": db.query(func.count(RawMention.id)).scalar(),
            "distinct_authors": db.query(func.count(func.distinct(RawMention.author_handle))).scalar(),
            "people_entities": db.query(func.count(Entity.id)).filter(Entity.type == "person").scalar(),
            "by_platform": {k: int(v) for k, v in by_platform.items()},
            "by_source_type": {k: int(v) for k, v in by_source_type.items()},
            "sentiment": {k: int(v) for k, v in sentiment_counts.items()},
            "last_24h": _mentions_since(24),
            "last_7d": _mentions_since(24 * 7),
            "last_30d": _mentions_since(24 * 30),
        }

        # --- pipeline ---
        recent_runs = db.query(IngestionRun).order_by(IngestionRun.started_at.desc().nullslast()).limit(10).all()
        pipeline = {
            "running": db.query(func.count(IngestionRun.id)).filter(IngestionRun.status == "running").scalar(),
            "failed_recent": sum(1 for r in recent_runs if r.status == "failed"),
            "recent": [
                {
                    "politician": (db.get(Politician, r.politician_id).name if db.get(Politician, r.politician_id) else "?"),
                    "status": r.status,
                    "mentions": (r.stats or {}).get("mentions_total"),
                    "credits_spent": r.credits_spent,
                    "started_at": str(r.started_at),
                    "duration_s": ((r.finished_at - r.started_at).total_seconds() if r.finished_at and r.started_at else None),
                }
                for r in recent_runs
            ],
        }

        # --- reports ---
        reports = {
            "total": db.query(func.count(IntelligenceReport.id)).scalar(),
            "last_7d": db.query(func.count(IntelligenceReport.id)).filter(
                IntelligenceReport.generated_at >= now - timedelta(days=7)
            ).scalar(),
        }

        # --- alerts ---
        alert_sev = dict(
            db.query(Alert.severity, func.count(Alert.id)).group_by(Alert.severity).all()
        )
        recent_alerts = db.query(Alert).order_by(Alert.created_at.desc()).limit(5).all()
        alerts = {
            "by_severity": {k: int(v) for k, v in alert_sev.items()},
            "recent": [
                {"severity": a.severity, "kind": a.kind, "headline": a.headline, "created_at": str(a.created_at)}
                for a in recent_alerts
            ],
        }

        system = {
            "db_ok": True,
            "scheduler_enabled": settings.ingestion_refresh_hours > 0,
            "refresh_hours": settings.ingestion_refresh_hours,
            "prewarm_names": [n.strip() for n in settings.prewarm_names.split(",") if n.strip()],
            "uptime_seconds": int(time.time() - _PROCESS_START),
            "generated_at": str(now),
        }
        return {"credits": credits, "sources": sources, "corpus": corpus,
                "pipeline": pipeline, "reports": reports, "alerts": alerts, "system": system}
    finally:
        db.close()


@app.get("/api/admin/metrics")
def admin_metrics(x_api_key: str | None = Header(default=None)):
    """Everything about the running system in one payload — the mission-control
    feed. Cached ~30s so dashboard polling never hammers the DB or the balance
    API."""
    _require_api_key(x_api_key)
    if _metrics_cache["data"] is None or time.time() - _metrics_cache["at"] > _METRICS_TTL:
        _metrics_cache["data"] = _admin_metrics()
        _metrics_cache["at"] = time.time()
    return {"ok": True, "metrics": _metrics_cache["data"]}


@app.get("/api/admin/timeseries")
def admin_timeseries(metric: str = "mentions", days: int = 30, x_api_key: str | None = Header(default=None)):
    """Daily buckets for the dashboard charts."""
    _require_api_key(x_api_key)
    from datetime import date, timedelta

    from sqlalchemy import func

    from engine.db.models import LlmUsage, RawMention

    db = SessionLocal()
    try:
        if metric == "llm_cost":
            rows = db.query(LlmUsage.day, LlmUsage.input_tokens, LlmUsage.output_tokens).filter(
                LlmUsage.day >= date.today() - timedelta(days=days)
            ).order_by(LlmUsage.day).all()
            series = [
                {
                    "date": str(d),
                    "usd": round((i / 1e6) * settings.anthropic_price_in + (o / 1e6) * settings.anthropic_price_out, 2),
                }
                for d, i, o in rows
            ]
        else:
            rows = (
                db.query(func.date(RawMention.posted_at), func.count(RawMention.id))
                .filter(RawMention.posted_at >= datetime.utcnow() - timedelta(days=days))
                .group_by(func.date(RawMention.posted_at))
                .order_by(func.date(RawMention.posted_at))
                .all()
            )
            series = [{"date": str(d), "count": int(c)} for d, c in rows]
        return {"ok": True, "metric": metric, "series": series}
    finally:
        db.close()


@app.get("/api/admin/run-state")
def run_state(name: str, kind: str = "report",
              x_api_key: str | None = Header(default=None)):
    """What a run is doing RIGHT NOW, without going through the report shaper.

    Everything a reader can see about a live run — sections, warnings, the
    stage ledger — reaches the page inside a SHAPED report payload. So when
    shaping is what fails, the failure cannot be reported: the diagnosis needs
    the very thing that is broken. A run stuck for sixteen minutes had no way
    to say why, and neither did I.

    This reads the raw job and progress row and reports them flat. It cannot be
    defeated by the bug it exists to diagnose.
    """
    _require_api_key(x_api_key)
    subject_key = _subject_key(name)
    row = _read_progress(subject_key, kind) or {}

    live = None
    for job_id, job in list(_jobs.items()):
        subject = job.get("subject") or ()
        if subject and subject[0] == name.strip().lower():
            live = {
                "job_id": job_id,
                "status": job.get("status"),
                "stage": job.get("stage"),
                "age_seconds": round(time.time() - job.get("created_at", time.time())),
                # The two fields the page needs to draw anything. If `partial`
                # is null while a run is running, sections are being produced
                # and NOT reaching the browser — which is the difference
                # between a slow run and a broken one.
                "has_partial": bool(job.get("partial")),
                "sections_ready": job.get("sections_ready") or [],
                "error": job.get("error"),
            }
            break

    return {
        "ok": True,
        "subject": subject_key,
        "live_job": live,
        "stored_progress": {
            "status": row.get("status"),
            "stage": row.get("stage"),
            "sections_ready": row.get("sections_ready") or [],
            "has_payload": bool(row.get("payload")),
            "started_at": row.get("started_at"),
            "updated_at": row.get("updated_at"),
            "error": row.get("error"),
        },
        # The ledger and health of whatever run this process last executed.
        "stages": stages_mod.current().summary(),
        "model_health": health_mod.current().summary(),
        "reading": (
            "sections_ready empty while status is running means the pipeline has "
            "not published anything yet — look at `stages.failed` and `model_health`. "
            "sections_ready non-empty but has_partial false means sections ARE being "
            "produced and are failing to reach the browser."
        ),
    }


@app.post("/api/admin/purge")
def admin_purge(
    confirm: str = "",
    subject: str | None = None,
    dry_run: bool = False,
    x_api_key: str | None = Header(default=None),
):
    """Delete accumulated run data. IRREVERSIBLE.

    The corpus compounds by design, which is right in production and wrong
    while the pipeline is being rebuilt underneath it: every run then shows
    material collected by a version of the system that no longer exists, and
    the page renders the last stored payload for a subject before the new run
    has produced anything — so old output appears instantly and reads as new.

    Requires the API key AND the confirmation phrase. `dry_run=true` reports
    what is there and deletes nothing.
    """
    _require_api_key(x_api_key)
    from engine.admin import purge as purge_mod
    from engine.db.session import SessionLocal

    db = SessionLocal()
    try:
        if dry_run:
            present = {t: n for t, n in purge_mod.counts(db).items() if n}
            return {"ok": True, "dry_run": True, "rows": present,
                    "total_rows": sum(present.values()),
                    "preserved": purge_mod.PRESERVED_BY_DEFAULT,
                    "hint": f"repeat with confirm={purge_mod.CONFIRMATION} to delete"}
        try:
            result = (purge_mod.purge_subject(db, subject, confirm) if subject
                      else purge_mod.purge_all(db, confirm))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # In-process job results outlive the database rows they came from, so
        # a purge that left them would serve the deleted report from memory on
        # the very next request.
        _jobs.clear()
        return {"ok": True, **result}
    finally:
        db.close()


@app.get("/api/admin/model-check")
def model_check(x_api_key: str | None = Header(default=None)):
    """Is the analysis model answering RIGHT NOW? One cheap call, ten seconds.

    The single URL to hit before wondering why a report came back empty. A
    retired model, a rejected key and an exhausted balance all produce the same
    empty report; this says which one it is and what to change."""
    _require_api_key(x_api_key)
    from engine import llm

    try:
        result = health_mod.preflight()
    except health_mod.PreflightFailed as failed:
        return {"ok": False, "answering": False, **failed.to_dict(),
                "configured_model": llm.strong_model(),
                "configured_bulk_model": llm.bulk_model(),
                "backend": llm.provider()}
    return {"ok": True, "answering": True, **result,
            "configured_model": llm.strong_model(),
            "configured_bulk_model": llm.bulk_model(),
            "note": "the model answered a minimal JSON request"}


@app.get("/api/admin/quality")
def admin_quality(name: str | None = None, limit: int = 20,
                  x_api_key: str | None = Header(default=None)):
    """The measured quality of recent reports, newest first.

    Phase 0 of docs/FOUNDATION.md: a change is better when these numbers say
    so on the same subjects — read in full, sentences a source backs, figures
    and citations that match nothing — not when one report looks better by
    eye. Each row names the commit that wrote it, so a deploy can be judged
    against the one before. Behind the API key because it names subjects.

    Reads the measurement out of the stored payload in the database rather
    than loading whole reports: twenty payloads are megabytes."""
    _require_api_key(x_api_key)
    from sqlalchemy import func

    from engine.db.models import IntelligenceReport

    db = SessionLocal()
    try:
        query = (db.query(IntelligenceReport.id, IntelligenceReport.generated_at,
                          IntelligenceReport.window_start, IntelligenceReport.window_end,
                          IntelligenceReport.payload["quality"], Politician.name)
                 .join(Politician, Politician.id == IntelligenceReport.politician_id))
        if name and name.strip():
            query = query.filter(func.lower(Politician.name) == name.strip().lower())
        rows = (query.order_by(IntelligenceReport.generated_at.desc())
                .limit(max(1, min(int(limit), 100))).all())
        return {"ok": True, "reports": [
            {"report_id": str(rid), "subject": subject, "generated_at": str(at),
             "window": [str(start), str(end)],
             # Absent on reports written before the measurement existed —
             # said, rather than shown as a report with nothing wrong.
             "quality": measured or {"measured": False,
                                     "error": "written before reports were measured"}}
            for rid, at, start, end, measured, subject in rows]}
    finally:
        db.close()


@app.get("/api/admin/facts")
def admin_facts(name: str, limit: int = 100, kind: str | None = None, grouped: bool = False,
                x_api_key: str | None = Header(default=None)):
    """The verified facts held on a subject, newest source first, each with
    the quote it rests on and where that quote was printed — and how much
    of the corpus has been read to get them. The way to check, before a
    report is written from them, that the store holds what the sources say.
    Behind the API key: it names a subject."""
    _require_api_key(x_api_key)
    from sqlalchemy import func

    from engine.db.models import Fact, FactExtraction

    db = SessionLocal()
    try:
        politician = db.query(Politician).filter(
            func.lower(Politician.name) == name.strip().lower()).first()
        if politician is None:
            raise HTTPException(status_code=404, detail="no subject by that name")
        query = (db.query(Fact, RawMention)
                 .outerjoin(RawMention, RawMention.id == Fact.mention_id)
                 .filter(Fact.politician_id == politician.id))
        if kind:
            query = query.filter(Fact.kind == kind)
        rows = (query.order_by(Fact.published_at.desc().nullslast())
                .limit(max(1, min(int(limit), 500))).all())
        reads = dict(db.query(FactExtraction.status, func.count(FactExtraction.id))
                     .filter(FactExtraction.politician_id == politician.id)
                     .group_by(FactExtraction.status).all())
        totals = db.query(func.coalesce(func.sum(FactExtraction.facts), 0),
                          func.coalesce(func.sum(FactExtraction.dropped_unverified), 0)).filter(
            FactExtraction.politician_id == politician.id).one()
        from engine.reports import outlets

        def _publisher(m):
            if m is None:
                return None
            raw = m.raw_payload or {}
            return outlets.publisher({"platform": m.platform, "source_type": m.source_type,
                                      "author_handle": m.author_handle, "outlet": raw.get("outlet"),
                                      "outlet_domain": raw.get("outlet_domain")})

        listed = [{"kind": f.kind, "status": f.status, "statement": f.statement,
                   "quote": f.quote, "actor": f.actor, "topic": f.topic,
                   "published_at": str(f.published_at) if f.published_at else None,
                   "publisher": _publisher(m) or f.source_key,
                   "source_id": f.source_key, "source_text": m.text if (m and grouped) else None,
                   "url": ((m.raw_payload or {}).get("url") or m.source_url) if m else None}
                  for f, m in rows]
        out = {"ok": True, "subject": politician.name,
               "sources_read": reads.get("read", 0), "sources_failed": reads.get("failed", 0),
               "facts_kept": int(totals[0]), "dropped_unverified": int(totals[1])}
        if grouped:
            from engine.evidence import facts as facts_store

            out["findings"] = facts_store.corroborate(listed)
            for finding in out["findings"]:
                for source in finding["sources"]:
                    source.pop("source_text", None)
        else:
            for row in listed:
                row.pop("source_text", None)
            out["facts"] = listed
        return out
    finally:
        db.close()


@app.get("/api/admin/source-check")
def source_check(q: str = "William Ruto", x_api_key: str | None = Header(default=None)):
    """Live per-source probe: runs EVERY connector against a sample query RIGHT
    NOW and returns how many items each returned (or the error). This is the
    single URL that shows production reality — which free sources actually
    deliver data on this deploy vs. which are down/credit-exhausted/mis-egressed.
    Never raises; each source is isolated."""
    _require_api_key(x_api_key)
    from datetime import datetime, timedelta

    we = datetime.utcnow()
    ws = we - timedelta(days=365)
    aliases = [q.strip().split()[-1]] if q.strip() else []

    def _probe(build):
        try:
            connector = build()
            items = connector.fetch(q, aliases, ws, we)
            sample = items[0].get("text", "")[:120] if items else None
            # A connector that swallows its own failures per-source can report
            # why it came back empty. Without that, a blocked host and a
            # genuine absence of data are the same zero.
            return {"items": len(items), "sample": sample,
                    "error": getattr(connector, "last_error", None)}
        except Exception as exc:  # noqa: BLE001
            return {"items": 0, "error": f"{type(exc).__name__}: {exc}"[:200], "sample": None}

    from engine.ingestion.gdelt_connector import GdeltConnector
    from engine.ingestion.google_news_rss_connector import GoogleNewsRssConnector
    from engine.ingestion.reddit_connector import RedditConnector
    from engine.ingestion.wayback_connector import WaybackConnector
    from engine.ingestion.wikipedia_connector import WikipediaConnector
    from engine.ingestion.youtube_connector import YouTubeConnector

    # Every connector `plan_run` can schedule belongs here. Wayback was enabled
    # by default, scheduled on every single run, and absent from this table —
    # so whether it returned anything was unknowable from outside. That is the
    # same blind spot that let the discovery layer sit dead for weeks.
    probes = {
        "gdelt": (GdeltConnector, settings.enable_gdelt),
        "google_news": (GoogleNewsRssConnector, settings.enable_google_news),
        "reddit": (RedditConnector, settings.enable_reddit),
        "youtube": (YouTubeConnector, settings.enable_youtube),
        "wikipedia": (WikipediaConnector, settings.enable_wikipedia),
        "wayback": (WaybackConnector, settings.enable_wayback),
    }
    if settings.enable_scweet:
        from engine.ingestion.scweet_connector import ScweetConnector

        probes["scweet_x"] = (ScweetConnector, True)
    if settings.enable_twscrape:
        from engine.ingestion.twscrape_connector import TwscrapeConnector

        probes["twscrape_x"] = (TwscrapeConnector, True)

    results = {}
    for name, (cls, enabled) in probes.items():
        results[name] = {"enabled": enabled, **(_probe(cls) if enabled else {"items": 0, "error": "disabled", "sample": None})}

    total = sum(r["items"] for r in results.values())
    # Which social backbone would serve a run right now, and why. Makes the
    # paid->free failover visible instead of something you infer from thin data.
    from engine.ingestion.orchestrator import resolve_social_tier

    tier, balance, reason = resolve_social_tier()

    # Discovery is probed separately: it returns documents, not mentions, and
    # its health (is SearXNG reachable? is the JSON API enabled?) is the single
    # biggest factor in how much archived/obscure material a run can find.
    discovery: dict = {"enabled": bool(settings.enable_discovery and settings.searxng_url)}
    if discovery["enabled"]:
        try:
            from engine.ingestion.discovery_connector import DiscoveryConnector

            connector = DiscoveryConnector()
            found = connector.discover([q])
            discovery.update(
                {"results": len(found), "error": connector.last_error,
                 "sample": found[0]["url"] if found else None,
                 # Both, because the difference between them IS the bug we hit:
                 # a Render internal hostname pasted as `service:port` has no
                 # scheme, requests refuses it, and the whole discovery layer
                 # goes quiet.
                 "searxng_url": settings.searxng_url,
                 "resolved_url": connector.base_url}
            )
            if not found:
                discovery["hint"] = (
                    connector.last_error
                    or "0 results — check the instance is up and `json` is in its search.formats"
                )
        except Exception as exc:  # noqa: BLE001
            discovery.update({"results": 0, "error": f"{type(exc).__name__}: {exc}"[:200]})
    else:
        discovery["hint"] = "set SEARXNG_URL to enable web discovery"

    # Which fetch tier is available to get past a bot wall. A thin corpus is
    # usually blocked rather than absent, and that distinction is invisible
    # unless the tiers report themselves.
    from engine.ingestion import fetch_backend

    return {
        "ok": True,
        "query": q,
        "checked_at": str(we),
        "fetch_backends": fetch_backend.availability(),
        "total_items": total,
        "working_sources": [n for n, r in results.items() if r["items"] > 0],
        "social_tier": {"tier": tier, "socialcrawl_balance": balance, "reason": reason},
        "discovery": discovery,
        "sources": results,
    }


_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
# THE frontend. Singular, deliberately.
#
# There used to be a second file at web/index.html which was what this route
# actually served, while every edit went to pulse_app.html. index.html was a
# BUILD ARTIFACT — pulse_app.html wrapped in a document skeleton by a shell
# one-liner in web/README.md — and nothing re-ran that step, so the two drifted
# silently: the backend shipped new behaviour on each deploy and the page never
# changed. The app contradicted its own API and every symptom read as a backend
# fault.
#
# The artifact is gone and the wrap happens here instead. One source file, no
# build step to forget, and nothing that can go stale between them.
FRONTEND_HTML = _WEB_DIR / "pulse_app.html"

# pulse_app.html is a FRAGMENT: it opens on <style> and closes on </script>.
# Serving it raw drops the browser into quirks mode, loses the charset (so the
# em dashes and curly quotes throughout the copy mojibake) and loses the
# viewport meta (so every phone gets a desktop layout).
_FRONTEND_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#070b12">
<title>Mũũgĩ</title>
<meta name="description" content="Mũũgĩ intelligence platform.">
<style>html,body{margin:0;background:#070b12}</style>
</head>
<body>
"""
_FRONTEND_TAIL = "\n</body>\n</html>\n"


def render_frontend_document() -> str:
    """The complete HTML document, wrapped around the single source file."""
    return _FRONTEND_HEAD + FRONTEND_HTML.read_text(encoding="utf-8") + _FRONTEND_TAIL


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the Mũũgĩ frontend from the engine itself, so the app and API
    share one origin — no separate hosting, no CORS setup, no demo mode. Just
    open the engine's URL and it IS the live app. (Falls back to a tiny notice
    if the web bundle wasn't shipped.)"""
    if FRONTEND_HTML.exists():
        # no-cache, not no-store: the browser still revalidates and still gets a
        # cheap 304 when nothing changed, but it can never serve a copy from
        # before the last deploy. Without this a stale page survives every
        # redeploy, the backend moves on without it, and the two disagree in
        # ways that look like backend bugs.
        return HTMLResponse(
            render_frontend_document(),
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )
    return HTMLResponse(
        "<h1>Mũũgĩ engine</h1><p>API is live. Frontend bundle not found — "
        "hit <a href='/api/health'>/api/health</a>.</p>",
        status_code=200,
    )



def _build_identity(commit: str) -> dict:
    """Which code is actually running, from whatever evidence exists.

    `build` reported "local" on Railway because it read only Render's
    variables, so when a fix appeared not to work there was no way to tell
    whether it had been deployed — and this file's own standing rule is
    never to diagnose a cause you have not checked. A deploy you cannot
    identify makes every diagnosis after it a guess.

    Env vars are per-host and some are build-time only, so they are the
    first choice and not the only one. `/app/BUILD_STAMP` is written during
    the image build and is true on every host; the mtime of this very file
    is the last resort, and it is the time the source was copied into the
    image, which is close enough to answer "is this today's deploy?".
    """
    out: dict = {"commit": (commit or "")[:7] or None, "source": None}
    if out["commit"]:
        out["source"] = "host environment"

    try:
        from engine import build as build_info

        lines = build_info.read_stamp()
        baked = lines[0] if lines else ""
        out["built_at"] = lines[1] if len(lines) > 1 else None
        if baked and not out["commit"]:
            out["commit"] = baked[:7]
            out["source"] = "image build stamp"
    except Exception:  # noqa: BLE001 — identity must never break health
        out["built_at"] = None

    if not out.get("built_at"):
        try:
            out["built_at"] = datetime.utcfromtimestamp(
                os.path.getmtime(os.path.abspath(__file__))).isoformat() + "Z"
            out["source"] = out["source"] or "source file timestamp"
        except Exception:  # noqa: BLE001
            pass

    out["label"] = out["commit"] or (
        f"built {out['built_at']}" if out.get("built_at") else "unknown")
    if not out["commit"]:
        out["hint"] = (
            "No commit is visible to the running container. Compare "
            "`built_at` with when you last pushed — if it is older, the "
            "deploy has not happened. Railway's Deployments tab shows the "
            "commit it actually built.")
    return out


def _schema_state(db) -> dict:
    """Is the database actually migrated to the code's head revision?

    `start.sh` deliberately starts the server even when `alembic upgrade
    head` fails, because a container that vanishes tells an operator
    nothing. That is the right call and it has a consequence worth being
    able to see: the app then runs against an older schema than the models
    declare, and the first symptom is every mention INSERT failing on a
    column that does not exist — which surfaces as "collection returned
    nothing", a sentence about the subject rather than about the deploy.

    So report it. An operator who has just pushed a migration should be able
    to confirm it ran without generating a report to find out, which is the
    same reason `analysis` is next door.
    """
    out: dict = {}
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        root = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        config = Config(os.path.join(root, "alembic.ini"))
        config.set_main_option("script_location", os.path.join(root, "alembic"))
        heads = set(ScriptDirectory.from_config(config).get_heads())
        out["code_head"] = sorted(heads)[0] if heads else None

        # The caller's session, not a new one: `health` already has a
        # connection open, and in tests the bound engine is the fixture's
        # rather than the module's — a check that opens its own cannot see
        # the database the rest of the request is talking to.
        applied = {row[0] for row in db.execute(
            text("SELECT version_num FROM alembic_version")).fetchall()}
        out["database_at"] = sorted(applied)[0] if applied else None
        out["migrated"] = bool(heads) and applied == heads
        if not out["migrated"]:
            out["hint"] = (
                "The database is not at the code's head revision, so the app "
                "is running against an older schema than its models declare. "
                "Redeploy and check the start-up log for the alembic step — "
                "the server starts even when that step fails, on purpose.")
    except Exception as exc:  # noqa: BLE001 — a health check must answer
        out["migrated"] = None
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out

def _model_backend_state() -> dict:
    """Is there a credential for the model backend at all?

    A live run died with "Missing Authentication header" — the provider
    received no key. The engine already knew: `configuration_problems()`
    says LLM_API_KEY is required and unset. Nobody asked it.

    Two reasons nobody asked, both fixed here. `validate_for_startup()` was
    registered on `engine/main.py`'s app (since deleted) while production
    ran `engine.api_server:app`, so the fail-closed check never ran in a
    deployment. And nothing reported configuration anywhere an operator
    could see it, so "is my key set?" could only be answered by generating
    a whole report and reading the error.

    Presence only. Never the value, never a prefix, never a length — this
    endpoint is public, and a key is not less leaked for being leaked four
    characters at a time.
    """
    provider = (settings.llm_provider or "anthropic").strip().lower()
    key = (settings.anthropic_api_key if provider == "anthropic"
           else settings.llm_api_key)
    state = {
        "provider": provider,
        # Whether a report run on this deployment may be sold. Everything
        # but the Anthropic path is stamped test-grade in the UI, and an
        # operator should be able to see that before spending a run.
        "production_grade": provider == "anthropic",
        "key_variable": ("ANTHROPIC_API_KEY" if provider == "anthropic"
                         else "LLM_API_KEY"),
        "key_present": bool((key or "").strip()),
        "model": (settings.anthropic_model if provider == "anthropic"
                  else settings.llm_model),
        "problems": settings.configuration_problems(),
    }
    if not state["key_present"]:
        state["hint"] = (
            f"{state['key_variable']} is empty or unset in this container, so "
            "requests go out with no credential and the provider answers 401 "
            "'Missing Authentication header'. If you believe you set it, check "
            "you set it on THIS service and environment, and that the service "
            "was redeployed afterwards — a variable added after the running "
            "deployment was created does not reach it.")
    return state


def _analysis_capability() -> dict:
    """Can this deployment do the fuller entity analysis, and if not, why?

    Three separable facts, because they have three different remedies: the
    setting is off, the model is missing from the image, or loading it
    raised. Reporting "low memory" for all three is how an operator ends up
    changing a setting that was already correct.
    """
    out = {"low_memory": settings.low_memory}
    try:
        from engine.processing.entities import get_nlp

        pipes = list(get_nlp().pipe_names)
        out["entity_model"] = "loaded" if "ner" in pipes else "blank"
        out["pipeline"] = pipes
    except Exception as exc:  # noqa: BLE001 — a health check must answer
        out["entity_model"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"[:200]
    # The consequence, spelled out, so nobody has to join the two up.
    out["grounding_method"] = ("ner" if out.get("entity_model") == "loaded"
                               else "capitalised-names")
    if out["grounding_method"] != "ner":
        out["hint"] = (
            "LOW_MEMORY is on; set it to false on an instance with ~1GB."
            if settings.low_memory else
            "LOW_MEMORY is already off, so this is not a settings problem — "
            "en_core_web_sm is not loadable here. Check that the image's "
            "`python -m spacy download en_core_web_sm` step ran."
        )
    return out


@app.get("/api/auth-check")
def auth_check(x_api_key: str | None = Header(default=None)):
    """Does this key open the engine? Nothing else.

    Settings said "Connected ✓" after checking only that /api/health
    answered, which it does for anyone — so a wrong key looked connected
    until the first report was refused. A green tick for a check that never
    ran is this product's founding rule broken on its own settings page.
    """
    _require_api_key(x_api_key)
    return {"ok": True, "key_required": bool(_normalise_key(settings.pulse_api_key))}


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
    # Is there a database at all, and can we reach it?
    #
    # `ok: true` used to be hardcoded. On a first deploy with no DATABASE_URL
    # the app now starts rather than crashing — which is right, because a
    # container that vanishes tells an operator nothing — but a health check
    # that then reports "ok" would be the same lie in a new place. It must
    # distinguish "running and working" from "running and useless".
    # "configured" asks whether this process has a database URL bound at all,
    # which is NOT the same as reading settings.database_url: tests and local
    # development bind the engine from elsewhere, and a check that only looks
    # at the raw setting reports a perfectly working app as unconfigured.
    # Ask the engine what it is actually pointed at.
    try:
        from engine.db.session import engine as _engine

        _bound = _engine.url
        database: dict = {"configured": bool(_bound.host or _bound.database),
                          "host": _bound.host, "reachable": False}
    except Exception:
        database = {"configured": False, "reachable": False}
    db = SessionLocal()
    try:
        from sqlalchemy import text

        from engine.db.models import IngestionRun

        db.execute(text("SELECT 1"))
        database["reachable"] = True
        schema = _schema_state(db)

        last_run = db.query(IngestionRun).order_by(IngestionRun.started_at.desc()).first()
        if last_run:
            supply["last_ingestion"] = {
                "status": last_run.status,
                "started_at": str(last_run.started_at),
                "mentions_total": (last_run.stats or {}).get("mentions_total"),
                "source_health": (last_run.stats or {}).get("source_health"),
            }
    except Exception as exc:
        database["error"] = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        db.close()
    # Unreachable database: there is nothing to compare the code's head
    # against, and reporting "migrated: false" would blame a migration for a
    # connection problem.
    schema = locals().get("schema") or {
        "migrated": None, "error": "the database could not be reached"}
    if not database["configured"]:
        database["hint"] = ("DATABASE_URL is not set. Set it to a Postgres "
                            "connection string and redeploy.")
    elif not database["reachable"]:
        database["hint"] = ("DATABASE_URL is set but the database could not be "
                            "reached. Check the password, that the database is "
                            "not suspended, and that the string keeps its "
                            "?sslmode=require suffix.")
    import os

    # Which commit is actually running.
    #
    # This read two Render-specific variables, so on Railway it reported
    # "local" — and when a fix did not appear to work there was no way for
    # anyone, including the person who wrote it, to tell whether it had been
    # deployed. Diagnosing a deploy you cannot identify is guessing, which is
    # the one thing this file keeps saying not to do.
    from engine import build as build_info

    commit = build_info.commit_from_environment()
    build = _build_identity(commit)
    return {
        # Serving a page is not the same as working. Without a database every
        # report will fail, and saying "ok" here would hide the one fact an
        # operator needs on a first deploy.
        "ok": database["reachable"],
        "database": database,
        "build": build["label"],
        # Everything known about WHICH CODE is running, because "is the fix
        # deployed?" was not answerable and a diagnosis of a deploy you
        # cannot identify is a guess.
        "build_detail": build,
        "local_ml": settings.use_local_ml,
        # What this deployment can actually do, as opposed to what its
        # environment variables say.
        #
        # An operator set LOW_MEMORY=false and had no way to confirm it had
        # taken effect without generating a whole report and reading a
        # banner — and that banner was guessing at the cause anyway. These
        # three facts answer it from one URL: the setting as parsed, whether
        # the entity model actually loads, and which extractor the grounding
        # check would therefore use. `settings.low_memory` is read rather
        # than the raw variable because the parse is the thing that matters:
        # a typo in the value is exactly the failure this is for.
        "analysis": _analysis_capability(),
        # Did the last deploy's migration actually run? See `_schema_state`.
        "schema": schema,
        "uptime_seconds": int(time.time() - _PROCESS_START),
        # Which model backend is actually live, so a provider switch can be
        # confirmed from outside without generating a report to find out.
        # `report_grade()` says which backend serves calls and whether its
        # output may be sold. `_model_backend_state()` adds the question that
        # could previously only be answered by generating a report and
        # reading how it died: is there a credential at all?
        #
        # Merged rather than added as a second "llm" key. A duplicate key in
        # a dict literal is not an error — the last one simply wins — so the
        # first attempt at this shipped, deployed, verified green, and was
        # silently discarded on the way out.
        "llm": {**_llm.report_grade(), **_model_backend_state()},
        "data_supply": supply,
    }

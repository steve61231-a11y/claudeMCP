from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy.orm import Session

from engine.config import settings
from engine.db.models import (
    Entity,
    IngestionRun,
    IntelligenceReport,
    MentionEntity,
    MentionNarrative,
    MentionSentiment,
    Narrative,
    NarrativeMetric,
    Politician,
    RawMention,
)
from engine.ingestion import orchestrator
from engine.intelligence import graph, influence, narratives as narrative_module
from engine.processing import cleaning, entities, sentiment
from engine.reports.generator import generate_report_payload
from engine.reports.sections import enrich_report_payload

# The expensive per-mention work is LLM round-trips (entity link, people
# extraction, sentiment). They're pure computation, so run them concurrently
# across mentions and keep all DB writes sequential.
_PER_MENTION_WORKERS = 10


def run_pipeline(
    db: Session,
    politician: Politician,
    period: str,
    window_start: datetime,
    window_end: datetime,
    credit_budget: float = 0.0,
) -> IntelligenceReport:
    """Full run: fan-out ingestion (all configured sources), then analysis."""
    run = run_ingestion(db, politician, window_start, window_end, credit_budget)
    return run_analysis(db, politician, period, window_start, window_end, ingestion_run=run)


def run_ingestion(
    db: Session,
    politician: Politician,
    window_start: datetime,
    window_end: datetime,
    credit_budget: float = 0.0,
) -> IngestionRun:
    run = orchestrator.plan_run(db, politician, window_start, window_end, credit_budget)
    return orchestrator.execute_run(db, run.id)


def run_analysis(
    db: Session,
    politician: Politician,
    period: str,
    window_start: datetime,
    window_end: datetime,
    ingestion_run: IngestionRun | None = None,
) -> IntelligenceReport:
    """Analysis over everything stored for the window: entity linking (only
    for mentions not yet checked, so re-analysis never re-pays the LLM),
    transcripts for top linked videos, sentiment, narratives, influence,
    graph and report."""
    politician_entity = _upsert_entity(db, "politician", politician.name)

    window_filter = (
        RawMention.politician_id == politician.id,
        RawMention.posted_at >= window_start,
        RawMention.posted_at <= window_end,
        RawMention.is_spam == 0,
    )

    people_counts: dict[str, dict] = {}
    unchecked = db.query(RawMention).filter(*window_filter, RawMention.link_checked == 0).all()

    def link_and_extract(mention: RawMention) -> tuple[dict | None, list[dict]]:
        link = entities.detect_entity_link(
            mention.text, politician.name, politician.aliases or [], politician.keywords or []
        )
        if not link and mention.source_type == "comment":
            # A comment under a post about the politician is relevant even
            # when the comment text never names them — that's most grassroots
            # reaction. The orchestrator tags comments with their parent post.
            parent = (mention.raw_payload or {}).get("_parent_post")
            if parent:
                link = {"matched": True, "match_type": "comment_on_linked_post", "confidence": 0.7}
        if not link:
            return None, []
        return link, entities.extract_people(mention.text, politician.name)

    with ThreadPoolExecutor(max_workers=_PER_MENTION_WORKERS) as pool:
        link_results = list(pool.map(link_and_extract, unchecked))

    for mention, (link, people) in zip(unchecked, link_results):
        mention.link_checked = 1
        if not link:
            continue
        db.add(
            MentionEntity(
                mention_id=mention.id,
                entity_id=politician_entity.id,
                confidence=link["confidence"],
                match_type=link["match_type"],
            )
        )
        # People co-mentioned with the politician — runs once per mention
        # (guarded by link_checked) and only on linked mentions.
        for person in people:
            person_entity = _upsert_entity(
                db, "person", person["name"], role=person.get("role"), affiliation=person.get("affiliation")
            )
            db.add(
                MentionEntity(
                    mention_id=mention.id, entity_id=person_entity.id, confidence=0.8, match_type="ner"
                )
            )
            record = people_counts.setdefault(
                person_entity.canonical_key,
                {"canonical_key": person_entity.canonical_key, "name": person_entity.name, "count": 0},
            )
            record["count"] += 1
            record["role"] = person_entity.role
            record["affiliation"] = person_entity.affiliation
    db.flush()

    if settings.socialcrawl_api_key and settings.transcripts_per_run > 0:
        _fetch_transcripts_for_top_videos(db, politician, politician_entity, window_start, window_end, ingestion_run)

    linked_mentions = (
        db.query(RawMention)
        .join(MentionEntity, MentionEntity.mention_id == RawMention.id)
        .filter(MentionEntity.entity_id == politician_entity.id, *window_filter)
        .all()
    )

    have_sentiment = {
        mid
        for (mid,) in db.query(MentionSentiment.mention_id)
        .filter(MentionSentiment.mention_id.in_([m.id for m in linked_mentions]))
        .all()
    }
    needing_sentiment = [m for m in linked_mentions if m.id not in have_sentiment]
    with ThreadPoolExecutor(max_workers=_PER_MENTION_WORKERS) as pool:
        sentiment_results = list(pool.map(lambda m: sentiment.analyze_sentiment(m.text), needing_sentiment))
    for mention, sentiment_result in zip(needing_sentiment, sentiment_results):
        db.add(
            MentionSentiment(
                mention_id=mention.id,
                sentiment=sentiment_result["sentiment"],
                intensity=sentiment_result["intensity"],
                context_tag=sentiment_result.get("context_tag"),
                confidence=sentiment_result["confidence"],
                source=sentiment_result["source"],
            )
        )
    db.flush()

    stored_mentions = [
        {
            "id": m.id,
            "platform": m.platform,
            "source_type": m.source_type,
            "author_handle": m.author_handle,
            "text": m.text,
            "posted_at": m.posted_at,
            "engagement": m.engagement_json or {},
            "language": m.language,
            "source_url": m.source_url,
        }
        for m in linked_mentions
    ]

    sentiment_rows = (
        db.query(MentionSentiment).filter(MentionSentiment.mention_id.in_([m["id"] for m in stored_mentions])).all()
    )
    sentiments_by_mention = {
        row.mention_id: {"sentiment": row.sentiment, "intensity": row.intensity} for row in sentiment_rows
    }

    built_narratives = narrative_module.build_narratives(stored_mentions)
    for n in built_narratives:
        narrative_row = Narrative(politician_id=politician.id, label=n["label"], description=n["description"])
        db.add(narrative_row)
        db.flush()
        for mid in n["mention_ids"]:
            db.add(MentionNarrative(mention_id=mid, narrative_id=narrative_row.id, confidence=1.0))
        db.add(
            NarrativeMetric(
                narrative_id=narrative_row.id,
                window_start=n["window_start"],
                window_end=n["window_end"],
                strength_score=n["strength_score"],
                growth_rate=n["growth_rate"],
            )
        )

    influence_ranking = influence.score_influence(stored_mentions, sentiments_by_mention)

    influencers = _classify_influencers(db, linked_mentions)
    for inf in influencers:
        _upsert_entity(db, "influencer", inf["handle"], role="creator", affiliation=inf["platform"])

    db.commit()

    # Neo4j writes happen only after the Postgres commit succeeds, so the graph
    # never records mentions/edges that don't actually exist in the source of
    # truth. upsert_mentions uses MERGE throughout, so re-running after a crash
    # here is safe and won't duplicate nodes/edges.
    graph.upsert_mentions(politician.id, politician.name, stored_mentions)
    graph.upsert_people(politician.id, list(people_counts.values()))
    graph.upsert_influencers(politician.id, influencers)
    # Postgres is the source of truth for the network view — Neo4j is not
    # provisioned in the Render deployment (and is stubbed in tests/demo), so
    # a Neo4j-only snapshot silently renders an empty map in production.
    # Neo4j results, when present, only fill in anything Postgres lacks.
    network_snapshot = {
        **graph.get_network_snapshot(politician.id),
        **_network_snapshot_from_db(db, politician, politician_entity),
    }

    payload = generate_report_payload(
        politician.name,
        window_start,
        window_end,
        stored_mentions,
        sentiments_by_mention,
        built_narratives,
        influence_ranking,
        network_snapshot,
    )
    payload = enrich_report_payload(
        politician.name,
        window_start,
        window_end,
        payload,
        mentions=stored_mentions,
        narratives=built_narratives,
    )
    if ingestion_run is not None:
        payload["data_provenance"] = {
            "ingestion_run_id": ingestion_run.id,
            "status": ingestion_run.status,
            "credits_spent": ingestion_run.credits_spent,
            **(ingestion_run.stats or {}),
        }
        payload["data_coverage"] = _coverage_summary(ingestion_run.stats or {})

    report = IntelligenceReport(
        politician_id=politician.id,
        period=period,
        window_start=window_start,
        window_end=window_end,
        payload=payload,
    )
    db.add(report)
    db.commit()
    return report


def _coverage_summary(stats: dict) -> dict:
    """Human-readable data-coverage disclosure for the report: which sources
    delivered, which failed and why. A thin report must say why it is thin."""
    health = stats.get("source_health") or {}
    ok, degraded, down = [], [], []
    notes = []
    for source, h in sorted(health.items()):
        if h.get("succeeded", 0) == h.get("attempted", 0) and h.get("attempted", 0) > 0:
            ok.append(source)
        elif h.get("succeeded", 0) > 0:
            degraded.append(source)
        else:
            down.append(source)
        failures = h.get("failures") or {}
        if failures.get("out_of_credits"):
            notes.append(f"{source}: data provider credits exhausted — top up to restore coverage")
        elif failures.get("endpoint_unavailable"):
            notes.append(f"{source}: provider endpoint unavailable this run")
        elif failures.get("upstream_error"):
            notes.append(f"{source}: partial — upstream errors during collection")
    balance = stats.get("credit_balance_after")
    return {
        "sources_ok": ok,
        "sources_degraded": degraded,
        "sources_down": down,
        "notes": sorted(set(notes)),
        "credit_balance": balance,
        "complete": not down and not degraded,
    }


def _canonical_key(entity_type: str, name: str) -> str:
    return f"{entity_type}:{name.strip().lower()}"


def _upsert_entity(
    db: Session, entity_type: str, name: str, role: str | None = None, affiliation: str | None = None
) -> Entity:
    key = _canonical_key(entity_type, name)
    entity = db.query(Entity).filter_by(canonical_key=key).first()
    if not entity:
        entity = Entity(name=name.strip(), type=entity_type, canonical_key=key, role=role, affiliation=affiliation)
        db.add(entity)
        db.flush()
        return entity
    # Fill gaps without overwriting operator-verified values.
    entity.role = entity.role or role
    entity.affiliation = entity.affiliation or affiliation
    return entity


def _network_snapshot_from_db(db: Session, politician: Politician, politician_entity: Entity) -> dict:
    """People-first network snapshot computed from Postgres, matching
    graph.get_network_snapshot's shape and adding person↔person co-mention
    edges (two people named together in the same mention)."""
    from sqlalchemy import func

    from engine.db.models import AuthorProfile

    mention_ids = (
        db.query(MentionEntity.mention_id)
        .join(RawMention, RawMention.id == MentionEntity.mention_id)
        .filter(MentionEntity.entity_id == politician_entity.id, RawMention.politician_id == politician.id)
        .subquery()
    )

    top_users = [
        {"handle": handle, "mentions": int(count)}
        for handle, count in (
            db.query(RawMention.author_handle, func.count(RawMention.id))
            .filter(RawMention.id.in_(mention_ids.select()))
            .group_by(RawMention.author_handle)
            .order_by(func.count(RawMention.id).desc())
            .limit(25)
            .all()
        )
    ]

    person_rows = (
        db.query(Entity, func.count(MentionEntity.id))
        .join(MentionEntity, MentionEntity.entity_id == Entity.id)
        .filter(Entity.type == "person", MentionEntity.mention_id.in_(mention_ids.select()))
        .group_by(Entity.id)
        .order_by(func.count(MentionEntity.id).desc())
        .limit(30)
        .all()
    )
    top_people = [
        {"name": e.name, "role": e.role, "affiliation": e.affiliation, "co_mentions": int(count)}
        for e, count in person_rows
    ]

    # person↔person edges: both named in the same mention text.
    me1, me2 = MentionEntity.__table__.alias("me1"), MentionEntity.__table__.alias("me2")
    e1, e2 = Entity.__table__.alias("e1"), Entity.__table__.alias("e2")
    pair_rows = db.execute(
        me1.join(me2, (me1.c.mention_id == me2.c.mention_id) & (me1.c.entity_id < me2.c.entity_id))
        .join(e1, e1.c.id == me1.c.entity_id)
        .join(e2, e2.c.id == me2.c.entity_id)
        .select()
        .with_only_columns(e1.c.name, e2.c.name, func.count().label("weight"))
        .where(
            e1.c.type == "person",
            e2.c.type == "person",
            me1.c.mention_id.in_(mention_ids.select()),
        )
        .group_by(e1.c.name, e2.c.name)
        .order_by(func.count().desc())
        .limit(60)
    ).all()
    people_edges = [{"from": a, "to": b, "weight": int(w)} for a, b, w in pair_rows]

    influencer_handles = {u["handle"] for u in top_users}
    influencers = [
        {"handle": p.handle, "platform": p.platform, "followers": p.follower_count, "posts": None}
        for p in (
            db.query(AuthorProfile)
            .filter(
                AuthorProfile.handle.in_(influencer_handles),
                AuthorProfile.follower_count >= settings.influencer_follower_threshold,
            )
            .order_by(AuthorProfile.follower_count.desc())
            .limit(30)
            .all()
        )
    ]

    return {
        "politician_id": politician.id,
        "top_users": top_users,
        "top_people": top_people,
        "top_influencers": influencers,
        "people_edges": people_edges,
    }


def _classify_influencers(db: Session, linked_mentions: list[RawMention]) -> list[dict]:
    """Authors of linked mentions with ≥ threshold followers — the client's
    definition of an influencer worth mapping, political creator or not."""
    from engine.db.models import AuthorProfile

    if not linked_mentions:
        return []
    posts_by_author: dict[tuple[str, str], int] = {}
    for mention in linked_mentions:
        key = (mention.platform, mention.author_handle)
        posts_by_author[key] = posts_by_author.get(key, 0) + 1

    handles = [h for (_, h) in posts_by_author]
    profiles = (
        db.query(AuthorProfile)
        .filter(
            AuthorProfile.handle.in_(handles),
            AuthorProfile.follower_count >= settings.influencer_follower_threshold,
        )
        .all()
    )
    return [
        {
            "platform": p.platform,
            "handle": p.handle,
            "followers": p.follower_count,
            "posts": posts_by_author.get((p.platform, p.handle), 1),
        }
        for p in profiles
        if (p.platform, p.handle) in posts_by_author
    ]


def _fetch_transcripts_for_top_videos(
    db: Session,
    politician: Politician,
    politician_entity: Entity,
    window_start: datetime,
    window_end: datetime,
    ingestion_run: IngestionRun | None,
) -> None:
    """Premium-priced transcripts, only for videos already linked to the
    politician, ranked by engagement, capped per run."""
    from engine.ingestion.socialcrawl_connector import SocialCrawlConnector, TRANSCRIPT_ENDPOINTS, credit_cost

    connector = SocialCrawlConnector()

    videos = (
        db.query(RawMention)
        .join(MentionEntity, MentionEntity.mention_id == RawMention.id)
        .filter(
            MentionEntity.entity_id == politician_entity.id,
            RawMention.politician_id == politician.id,
            RawMention.posted_at >= window_start,
            RawMention.posted_at <= window_end,
            RawMention.platform.in_(list(TRANSCRIPT_ENDPOINTS)),
            RawMention.source_type == "video",
        )
        .all()
    )
    existing_transcripts = {
        (m.platform, (m.raw_payload or {}).get("transcript_of"))
        for m in db.query(RawMention).filter(
            RawMention.politician_id == politician.id, RawMention.source_type == "transcript"
        )
    }

    def engagement_score(m: RawMention) -> int:
        e = m.engagement_json or {}
        return sum(int(e.get(k, 0) or 0) for k in ("likes", "shares", "comments", "views"))

    fetched = 0
    for video in sorted(videos, key=engagement_score, reverse=True):
        if fetched >= settings.transcripts_per_run:
            break
        payload = video.raw_payload or {}
        post_id = payload.get("id") or payload.get("video_id") or payload.get("post_id")
        if not post_id or (video.platform, str(post_id)) in existing_transcripts:
            continue
        try:
            transcript = connector.fetch_transcript(video.platform, str(post_id))
        except Exception:
            continue
        if ingestion_run is not None:
            ingestion_run.credits_spent += credit_cost(
                TRANSCRIPT_ENDPOINTS[video.platform]
            )
        if not transcript:
            continue
        fetched += 1
        text = cleaning.normalize_text(transcript)
        mention = RawMention(
            politician_id=politician.id,
            platform=video.platform,
            source_type="transcript",
            author_handle=video.author_handle,
            text=text,
            posted_at=video.posted_at,
            engagement_json=video.engagement_json or {},
            raw_payload={"transcript_of": str(post_id), "video_mention_id": video.id},
            content_hash=cleaning.content_hash(video.author_handle, text),
            is_spam=0,
            run_id=ingestion_run.id if ingestion_run else None,
            source_endpoint=TRANSCRIPT_ENDPOINTS[video.platform],
            source_url=video.source_url,
            fetched_at=datetime.utcnow(),
            language=orchestrator.queries.detect_language(text),
            link_checked=1,
        )
        db.add(mention)
        db.flush()
        db.add(
            MentionEntity(
                mention_id=mention.id,
                entity_id=politician_entity.id,
                confidence=0.9,
                match_type="video_transcript",
            )
        )
    db.flush()

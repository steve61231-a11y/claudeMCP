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
    politician_entity = db.query(Entity).filter_by(name=politician.name, type="politician").first()
    if not politician_entity:
        politician_entity = Entity(name=politician.name, type="politician")
        db.add(politician_entity)
        db.flush()

    window_filter = (
        RawMention.politician_id == politician.id,
        RawMention.posted_at >= window_start,
        RawMention.posted_at <= window_end,
        RawMention.is_spam == 0,
    )

    for mention in db.query(RawMention).filter(*window_filter, RawMention.link_checked == 0).all():
        mention.link_checked = 1
        link = entities.detect_entity_link(
            mention.text, politician.name, politician.aliases or [], politician.keywords or []
        )
        if link:
            db.add(
                MentionEntity(
                    mention_id=mention.id,
                    entity_id=politician_entity.id,
                    confidence=link["confidence"],
                    match_type=link["match_type"],
                )
            )
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
    for mention in linked_mentions:
        if mention.id in have_sentiment:
            continue
        sentiment_result = sentiment.analyze_sentiment(mention.text)
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
            "author_handle": m.author_handle,
            "text": m.text,
            "posted_at": m.posted_at,
            "engagement": m.engagement_json or {},
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

    db.commit()

    # Neo4j writes happen only after the Postgres commit succeeds, so the graph
    # never records mentions/edges that don't actually exist in the source of
    # truth. upsert_mentions uses MERGE throughout, so re-running after a crash
    # here is safe and won't duplicate nodes/edges.
    graph.upsert_mentions(politician.id, politician.name, stored_mentions)
    network_snapshot = graph.get_network_snapshot(politician.id)

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
    if ingestion_run is not None:
        payload["data_provenance"] = {
            "ingestion_run_id": ingestion_run.id,
            "status": ingestion_run.status,
            "credits_spent": ingestion_run.credits_spent,
            **(ingestion_run.stats or {}),
        }

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

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy.orm import Session

from engine.db.models import (
    Entity,
    IntelligenceReport,
    MentionEntity,
    MentionNarrative,
    MentionSentiment,
    Narrative,
    NarrativeMetric,
    Politician,
    RawMention,
)
from engine.ingestion import get_ingestion_connector
from engine.intelligence import graph, influence, narratives as narrative_module
from engine.processing import cleaning, entities, sentiment
from engine.reports.generator import generate_report_payload
from engine.reports.sections import enrich_report_payload

_PER_MENTION_WORKERS = 10


def _link_and_score(item: dict, politician: Politician) -> tuple[dict, dict, dict] | None:
    """Runs the two LLM-backed steps (entity-link, sentiment) for one mention.
    Pure computation, no DB access, so many of these can run concurrently."""
    link = entities.detect_entity_link(item["text"], politician.name, politician.aliases, politician.keywords)
    if not link:
        return None
    sentiment_result = sentiment.analyze_sentiment(item["text"])
    return item, link, sentiment_result


def run_pipeline(db: Session, politician: Politician, period: str, window_start: datetime, window_end: datetime) -> IntelligenceReport:
    raw_mentions = get_ingestion_connector(politician.name).fetch(politician.name, politician.aliases, window_start, window_end)
    cleaned = cleaning.clean_mentions(raw_mentions)
    cleaned = [m for m in cleaned if not m["is_spam"]]

    politician_entity = db.query(Entity).filter_by(name=politician.name, type="politician").first()
    if not politician_entity:
        politician_entity = Entity(name=politician.name, type="politician")
        db.add(politician_entity)
        db.flush()

    # The expensive part of this loop is two sequential LLM round-trips per
    # mention (entity-link, sentiment) — run them concurrently across mentions
    # rather than one mention at a time, then do the DB writes sequentially.
    with ThreadPoolExecutor(max_workers=_PER_MENTION_WORKERS) as pool:
        linked = [r for r in pool.map(lambda item: _link_and_score(item, politician), cleaned) if r is not None]

    stored_mentions: list[dict] = []
    for item, link, sentiment_result in linked:
        mention = RawMention(
            politician_id=politician.id,
            platform=item["platform"],
            source_type=item["source_type"],
            author_handle=item["author_handle"],
            text=item["text"],
            posted_at=item["posted_at"],
            engagement_json=item["engagement"],
            raw_payload=item["raw_payload"],
            content_hash=item["content_hash"],
            is_spam=0,
        )
        db.add(mention)
        db.flush()

        db.add(
            MentionEntity(
                mention_id=mention.id,
                entity_id=politician_entity.id,
                confidence=link["confidence"],
                match_type=link["match_type"],
            )
        )

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

        stored_mentions.append(
            {
                "id": mention.id,
                "platform": mention.platform,
                "author_handle": mention.author_handle,
                "text": mention.text,
                "posted_at": mention.posted_at,
                "engagement": mention.engagement_json,
            }
        )

    db.flush()

    mention_ids = [m["id"] for m in stored_mentions]
    sentiment_rows = db.query(MentionSentiment).filter(MentionSentiment.mention_id.in_(mention_ids)).all()
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
    payload = enrich_report_payload(politician.name, window_start, window_end, payload)

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

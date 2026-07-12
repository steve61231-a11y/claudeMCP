import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def gen_uuid():
    return str(uuid.uuid4())


class Politician(Base):
    __tablename__ = "politicians"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    # Multi-domain subject: not just politicians. One of
    # person | politician | organization | ministry | business | individual.
    # Kept on this table (no rename) so the whole pipeline stays compatible;
    # it only tunes acquisition queries and analyst phrasing.
    subject_type = Column(String, nullable=False, default="politician", server_default="politician")
    aliases = Column(ARRAY(String), default=list)
    keywords = Column(ARRAY(String), default=list)
    social_handles = Column(JSONB, default=dict)  # {"tiktok": "...", "youtube": "...", ...}
    titles = Column(ARRAY(String), default=list)  # "CS", "Waziri wa Fedha" — combined with surname in queries
    swahili_terms = Column(ARRAY(String), default=list)  # operator-approved Swahili/Sheng query terms
    tracked_hashtags = Column(ARRAY(String), default=list)  # without leading '#'
    created_at = Column(DateTime, default=datetime.utcnow)


class RawMention(Base):
    __tablename__ = "raw_mentions"
    __table_args__ = (
        UniqueConstraint(
            "politician_id", "platform", "content_hash", name="uq_raw_mentions_politician_platform_hash"
        ),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False)
    platform = Column(String, nullable=False)
    source_type = Column(String, nullable=False)  # post | comment
    author_handle = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    posted_at = Column(DateTime, nullable=False)
    engagement_json = Column(JSONB, default=dict)
    raw_payload = Column(JSONB, default=dict)
    content_hash = Column(String, index=True)
    is_spam = Column(Integer, default=0)
    # Provenance: which run/endpoint produced this row, and where it lives online.
    run_id = Column(UUID(as_uuid=False), ForeignKey("ingestion_runs.id"), nullable=True)
    source_endpoint = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    fetched_at = Column(DateTime, nullable=True)
    language = Column(String, nullable=True)  # "en" | "sw" | "und"
    author_platform_id = Column(String, nullable=True)
    # 1 once entity linking has been attempted, so re-analysis never re-pays
    # the LLM for mentions that already failed to link.
    link_checked = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("canonical_key", name="uq_entities_canonical_key"),)

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # politician|person|influencer|media|party|location|event
    # "type:normalized name" — dedupes the same person/org across runs.
    canonical_key = Column(String, nullable=True)
    role = Column(String, nullable=True)  # journalist|politician|party official|creator|...
    affiliation = Column(String, nullable=True)  # media house / party / organisation


class MentionEntity(Base):
    __tablename__ = "mention_entities"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), nullable=False)
    entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False)
    confidence = Column(Float, default=1.0)
    match_type = Column(String, default="direct")  # direct|alias|indirect_llm


class MentionSentiment(Base):
    __tablename__ = "mention_sentiment"

    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), primary_key=True)
    sentiment = Column(String, nullable=False)  # positive|neutral|negative
    intensity = Column(Integer, nullable=False)  # 1-5
    context_tag = Column(String, nullable=True)  # support|attack|concern|praise
    confidence = Column(Float, default=1.0)
    source = Column(String, default="local")  # local|llm


class Narrative(Base):
    __tablename__ = "narratives"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False)
    label = Column(String, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class MentionNarrative(Base):
    __tablename__ = "mention_narratives"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), nullable=False)
    narrative_id = Column(UUID(as_uuid=False), ForeignKey("narratives.id"), nullable=False)
    confidence = Column(Float, default=1.0)


class NarrativeMetric(Base):
    __tablename__ = "narrative_metrics"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    narrative_id = Column(UUID(as_uuid=False), ForeignKey("narratives.id"), nullable=False)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    strength_score = Column(Float, default=0.0)
    growth_rate = Column(Float, default=0.0)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False)
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    status = Column(String, nullable=False, default="pending")  # pending|running|completed|failed
    credit_budget = Column(Float, default=0.0)  # 0 = unlimited
    credits_spent = Column(Float, default=0.0)
    stats = Column(JSONB, default=dict)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IngestionTask(Base):
    """One (connector, platform, endpoint, query) fetch slice — the unit of
    parallelism and resumability. page_cursor persists across crashes so a
    restarted run continues where it stopped instead of re-paying for pages."""

    __tablename__ = "ingestion_tasks"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    run_id = Column(UUID(as_uuid=False), ForeignKey("ingestion_runs.id"), nullable=False, index=True)
    connector = Column(String, nullable=False)  # socialcrawl|newsapi|curated|mock
    platform = Column(String, nullable=False)
    endpoint = Column(String, nullable=False)
    query = Column(String, nullable=False, default="")
    params = Column(JSONB, default=dict)
    page_cursor = Column(Integer, default=1)
    status = Column(String, nullable=False, default="pending")  # pending|running|done|failed|skipped_budget
    credits_spent = Column(Float, default=0.0)
    result_count = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuthorProfile(Base):
    __tablename__ = "author_profiles"
    __table_args__ = (UniqueConstraint("platform", "handle", name="uq_author_profiles_platform_handle"),)

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    platform = Column(String, nullable=False)
    handle = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    follower_count = Column(Integer, nullable=True)
    profile_url = Column(String, nullable=True)
    last_refreshed = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IntelligenceReport(Base):
    __tablename__ = "intelligence_reports"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False)
    period = Column(String, nullable=False)  # daily|weekly|monthly
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    payload = Column(JSONB, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    """Early-warning event: something changed in a politician's conversation
    that their team should know about before they thought to ask."""

    __tablename__ = "alerts"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False, index=True)
    severity = Column(String, nullable=False)  # info|warning|critical
    kind = Column(String, nullable=False)  # narrative_surge|negative_spike|viral_post|new_amplifier
    headline = Column(String, nullable=False)
    detail = Column(String, nullable=True)
    evidence = Column(JSONB, default=list)  # [{mention_id, url, text, author, platform}]
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    delivered = Column(Integer, default=0)  # webhook delivery flag


class LlmUsage(Base):
    """Daily rollup of Anthropic token usage — the basis for real
    'Anthropic spend' on the admin dashboard (tokens x configured price)."""

    __tablename__ = "llm_usage"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    day = Column(Date, nullable=False, unique=True, index=True)
    calls = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)

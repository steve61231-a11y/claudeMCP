import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Column,
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
    aliases = Column(ARRAY(String), default=list)
    keywords = Column(ARRAY(String), default=list)
    social_handles = Column(JSONB, default=dict)  # {"tiktok": "...", "youtube": "...", ...}
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
    created_at = Column(DateTime, default=datetime.utcnow)


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # politician|influencer|media|party|location|event


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


class IntelligenceReport(Base):
    __tablename__ = "intelligence_reports"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False)
    period = Column(String, nullable=False)  # daily|weekly|monthly
    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)
    payload = Column(JSONB, nullable=False)
    generated_at = Column(DateTime, default=datetime.utcnow)

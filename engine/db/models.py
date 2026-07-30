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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
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
    # politician|person|influencer|media|party|location|event|organization|
    # company|policy|issue|contract|document
    type = Column(String, nullable=False)
    # "type:normalized name" — dedupes the same person/org across runs.
    canonical_key = Column(String, nullable=True)
    role = Column(String, nullable=True)  # journalist|politician|party official|creator|...
    affiliation = Column(String, nullable=True)  # media house / party / organisation
    # Temporal tracking: when this entity first/last appeared in the corpus —
    # the basis for "first appearance" weak-signal detection.
    aliases = Column(ARRAY(String), default=list)
    attributes = Column(JSONB, default=dict)  # typed extras (jurisdiction, registration no, ...)
    mention_count = Column(Integer, default=0)
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)


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


# ---------------------------------------------------------------------------
# Due-diligence intelligence core (Phase 0 foundation)
#
# The unit of intelligence is NOT a social-media mention. A `Document` is any
# piece of acquired evidence — a news article (live or archived), a web page
# discovered via metasearch, a filing, a transcript — stored with its FULL text
# so an obscure fact buried in a 1992 article is retrievable, not summarised
# away. Mentions and documents both act as evidence for entities, events and
# claims; every asserted claim must resolve to at least one of them.
# ---------------------------------------------------------------------------


class Document(Base):
    """Full-text evidence document (article / web page / archived record).

    Complements RawMention (social-post shaped) with long-form sources. The
    body is kept in full — retrieval, not truncation, is how we avoid losing
    context. `search_vector` powers Postgres full-text retrieval so analysts
    (and the RAG layer) can pull the exact passage that supports a claim.
    """

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("politician_id", "content_hash", name="uq_document_subject_hash"),)

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False, index=True)
    url = Column(Text, nullable=True)
    domain = Column(String, nullable=True, index=True)
    title = Column(Text, nullable=True)
    body = Column(Text, nullable=False, default="")  # full extracted text
    author = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True, index=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    # Provenance: which connector produced it and at which reliability tier.
    source = Column(String, nullable=True, index=True)  # searxng|gdelt|ccnews|wayback|google_news|...
    source_tier = Column(String, nullable=True)  # managed|free|archive
    doc_type = Column(String, nullable=True)  # article|profile|filing|transcript|page
    language = Column(String, nullable=True)
    content_hash = Column(String, nullable=True, index=True)
    # Relevance / disambiguation gate outcome (the "SHA-Kenya" guard).
    relevance_score = Column(Float, nullable=True)
    relevance_verdict = Column(String, nullable=True, index=True)  # on_topic|off_topic|ambiguous
    relevance_reason = Column(Text, nullable=True)
    # Pipeline bookkeeping — stages are idempotent and resumable.
    processed_stages = Column(JSONB, default=dict)  # {"classified": true, "extracted": true, ...}
    run_id = Column(UUID(as_uuid=False), ForeignKey("ingestion_runs.id"), nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)
    raw_payload = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


class Event(Base):
    """A real-world happening, resolved from many mentions/documents.

    Deduplication of *reporting* into *events* is what turns a pile of
    repetitive coverage into intelligence: 40 articles about one contract award
    become one event with 40 pieces of corroborating evidence.
    """

    __tablename__ = "events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    summary = Column(Text, nullable=True)
    event_type = Column(String, nullable=True, index=True)  # appointment|contract|court|scandal|statement|...
    occurred_at = Column(DateTime, nullable=True, index=True)
    occurred_precision = Column(String, nullable=True)  # day|month|year|unknown
    location = Column(String, nullable=True)
    # Corroboration is the basis of confidence: how many INDEPENDENT sources.
    corroboration_count = Column(Integer, default=0)
    independent_domains = Column(Integer, default=0)
    confidence = Column(Float, nullable=True)
    significance = Column(Float, nullable=True)  # for surfacing "small today, big later"
    dedupe_key = Column(String, nullable=True, index=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class EventEvidence(Base):
    """Links an event to each piece of evidence supporting it."""

    __tablename__ = "event_evidence"
    __table_args__ = (
        UniqueConstraint("event_id", "mention_id", "document_id", name="uq_event_evidence"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    event_id = Column(UUID(as_uuid=False), ForeignKey("events.id"), nullable=False, index=True)
    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), nullable=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=True)
    quote = Column(Text, nullable=True)  # the exact supporting passage
    role = Column(String, nullable=True)  # primary|corroborating|contradicting
    created_at = Column(DateTime, default=datetime.utcnow)


class EntityRelationship(Base):
    """Typed edge in the knowledge graph (Postgres-native).

    Generalises the people-graph beyond people: person->org, org->contract,
    person->event, entity->policy, with provenance and a time range so the graph
    can answer "who was connected to whom, when".
    """

    __tablename__ = "entity_relationships"
    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "rel_type", name="uq_entity_rel"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=True, index=True)
    source_entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False, index=True)
    target_entity_id = Column(UUID(as_uuid=False), ForeignKey("entities.id"), nullable=False, index=True)
    # works_for|allied_with|rival_of|involved_in|mentioned_with|located_in|
    # owns|contracted_by|family_of|concerns_policy|investigated_by ...
    rel_type = Column(String, nullable=False, index=True)
    weight = Column(Float, default=1.0)
    confidence = Column(Float, default=0.5)
    evidence = Column(JSONB, default=list)  # [{mention_id|document_id, quote}]
    evidence_count = Column(Integer, default=0)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)


class SourceCredibility(Base):
    """Per-source/author credibility, feeding confidence on every insight."""

    __tablename__ = "source_credibility"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    key = Column(String, nullable=False, unique=True, index=True)  # domain or @handle
    source_type = Column(String, nullable=True)  # mainstream|digital|blog|social|official|archive
    score = Column(Float, default=0.5)  # 0..1
    # Component breakdown so a score is explainable, never a magic number.
    components = Column(JSONB, default=dict)  # {type, independence, corroboration, history}
    corroboration_rate = Column(Float, nullable=True)
    observations = Column(Integer, default=0)
    notes = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)


class MentionClassification(Base):
    """Batched classifier output per mention/document (topic, language, etc.)."""

    __tablename__ = "mention_classifications"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), nullable=True, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=True, index=True)
    topic = Column(String, nullable=True, index=True)
    subtopics = Column(ARRAY(String), default=list)
    language = Column(String, nullable=True)
    content_kind = Column(String, nullable=True)  # report|opinion|rumour|primary_source|satire
    is_substantive = Column(Integer, default=1)  # 0 = passing/low-value reference
    confidence = Column(Float, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)


class Claim(Base):
    """An atomic assertion produced by the analysis layer.

    Every claim that reaches a report must be verifiable: the verification
    ("judge") agent resolves each one against stored evidence and records the
    verdict. Unsupported claims are never presented as fact.
    """

    __tablename__ = "claims"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False, index=True)
    report_id = Column(UUID(as_uuid=False), ForeignKey("intelligence_reports.id"), nullable=True, index=True)
    text = Column(Text, nullable=False)
    section = Column(String, nullable=True)  # which report section asserted it
    claim_type = Column(String, nullable=True)  # fact|relationship|trend|risk|anomaly
    # Verification outcome — the anti-hallucination record.
    status = Column(String, default="unverified", index=True)  # verified|unverified|contradicted|dropped
    confidence = Column(Float, nullable=True)
    evidence_count = Column(Integer, default=0)
    independent_sources = Column(Integer, default=0)
    verifier_note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ClaimEvidence(Base):
    """Citation: links a claim to the exact mention/document passage."""

    __tablename__ = "claim_evidence"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    claim_id = Column(UUID(as_uuid=False), ForeignKey("claims.id"), nullable=False, index=True)
    mention_id = Column(UUID(as_uuid=False), ForeignKey("raw_mentions.id"), nullable=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=True)
    quote = Column(Text, nullable=True)
    url = Column(Text, nullable=True)
    stance = Column(String, nullable=True)  # supports|contradicts
    credibility = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Snapshot(Base):
    """Per-run state capture powering temporal intelligence ("what changed").

    Diffing successive snapshots is how the system reports movement rather than
    re-describing the present each time.
    """

    __tablename__ = "snapshots"

    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    politician_id = Column(UUID(as_uuid=False), ForeignKey("politicians.id"), nullable=False, index=True)
    run_id = Column(UUID(as_uuid=False), ForeignKey("ingestion_runs.id"), nullable=True)
    taken_at = Column(DateTime, default=datetime.utcnow, index=True)
    # Counts + fingerprints of entities/events/relationships/narratives/sentiment.
    metrics = Column(JSONB, default=dict)
    entity_state = Column(JSONB, default=dict)
    narrative_state = Column(JSONB, default=dict)
    # Computed diff vs the previous snapshot.
    delta = Column(JSONB, default=dict)

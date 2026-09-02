import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from engine import health, stages
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
# `sentiment` was imported here and never called — per-mention scoring lives in
# engine.agents.score, which is batched. A dead import reads as a live stage.
from engine.processing import cleaning, entities
from engine.reports.generator import generate_report_payload
from engine.reports.sections import enrich_report_payload

# The expensive per-mention work is LLM round-trips (entity link, people
# extraction, sentiment). They're pure computation, so run them concurrently
# across mentions and keep all DB writes sequential.
_PER_MENTION_WORKERS = 10


def _document_corpus(db: Session, politician: Politician, window_start, window_end) -> list[dict]:
    """Discovered documents rendered in the same shape as mentions.

    The analysts and the map-reduce digest are dict-driven, so presenting
    documents this way lets long-form evidence be read alongside social posts
    with no change to those layers. Off-topic documents (flagged by the
    disambiguation gate) are excluded. Undated archive material is included —
    an old page is often exactly the point of due diligence.
    """
    from engine.db.models import Document

    rows = (
        db.query(Document)
        .filter(
            Document.politician_id == politician.id,
            (Document.relevance_verdict.is_(None)) | (Document.relevance_verdict != "off_topic"),
        )
        .order_by(Document.published_at.desc().nullslast())
        .limit(settings.document_corpus_limit)
        .all()
    )
    corpus: list[dict] = []
    for doc in rows:
        text = f"{doc.title}\n\n{doc.body}" if doc.title else (doc.body or "")
        if not text.strip():
            continue
        corpus.append(
            {
                "id": doc.id,
                "platform": doc.domain or "web",
                "source_type": "article",
                "author_handle": doc.author or doc.domain or "web",
                "text": text,
                "posted_at": doc.published_at or doc.fetched_at,
                "engagement": {},
                "language": doc.language,
                "source_url": doc.url,
            }
        )
    return corpus


def _as_corpus_dicts(rows) -> list[dict]:
    """RawMention rows in the shape every reader downstream expects.

    Defined once because the early corpus preview and the real analysis must
    describe the same corpus — two hand-rolled copies of this mapping would
    drift, and the preview would then quietly disagree with the report that
    replaces it.
    """
    return [
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
        for m in rows
    ]


def _per_mention_workers() -> int:
    """Fewer threads on a memory-constrained free instance so concurrent work
    can't exhaust RAM and get the worker OOM-killed (intermittent 502s), and
    never more than the operator's LLM concurrency ceiling.

    That ceiling exists to keep a rate-limited backend under its per-minute
    quota, and this is the highest-volume stage in the pipeline — it was the
    one place that ignored it."""
    from engine import llm

    return llm.concurrency(3 if settings.low_memory else _PER_MENTION_WORKERS)


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
    on_section=None,
) -> IntelligenceReport:
    """Analysis over everything stored for the window: entity linking (only
    for mentions not yet checked, so re-analysis never re-pays the LLM),
    transcripts for top linked videos, sentiment, narratives, influence,
    graph and report.

    `on_section(key, value)` fires as each payload section lands. A full run
    takes tens of minutes; without this the reader sees nothing at all until
    the last stage finishes, which is why depth and a usable wait looked like
    a trade-off. They are not — the sections just have to be published as they
    are made.
    """
    def publish(key, value):
        if on_section is None:
            return
        try:
            on_section(key, value)
        except Exception:  # noqa: BLE001 — never let a reader cost the report
            pass

    # Ask the model one trivial question before doing anything expensive. A run
    # that cannot reach its model produces an empty report that looks exactly
    # like a thin subject — that is how a retired OpenRouter preview model went
    # unnoticed while every stage silently degraded around it.
    health.reset()
    stages.reset()
    health.preflight()

    politician_entity = _upsert_entity(db, "politician", politician.name)

    window_filter = (
        RawMention.politician_id == politician.id,
        RawMention.posted_at >= window_start,
        RawMention.posted_at <= window_end,
        RawMention.is_spam == 0,
    )

    def _engagement(m: RawMention) -> int:
        e = m.engagement_json or {}
        return sum(int(e.get(k, 0) or 0) for k in ("likes", "shares", "comments", "views"))

    # The first thing a reader gets, and it costs nothing: volume, platform
    # spread, recency and the loudest mentions are arithmetic over rows we
    # already hold. This used to be computed alongside sentiment and narratives
    # and therefore arrived only after entity linking, people extraction and
    # whole-corpus scoring — the longest stretch of a run. A reader waited ten
    # minutes to be told how many mentions there were.
    if on_section is not None:
        try:
            from engine.reports.generator import corpus_preview

            preview = corpus_preview(
                _as_corpus_dicts(db.query(RawMention).filter(*window_filter).all()),
                window_end,
            )
            for _key, _value in preview.items():
                publish(_key, _value)
            # Publish the ledger straight after the preview. It was only
            # stamped on at the very END of a run, so a failure in the first
            # seconds — preflight, the preview itself, a publish that could not
            # be shaped — was invisible for the entire run. The reader saw a
            # spinner and no explanation, which is the state that makes a
            # working run and a dead one look identical.
            publish("section_status", stages.current().summary())
        except Exception as exc:  # noqa: BLE001 — a preview must never risk the report
            stages.current().failed("corpus_preview", exc)
            traceback.print_exc()

    _llm_cap = settings.low_memory_max_llm_mentions if settings.low_memory else settings.max_llm_mentions

    people_counts: dict[str, dict] = {}
    unchecked = db.query(RawMention).filter(*window_filter, RawMention.link_checked == 0).all()

    # Sources that fetch BY the subject's name are on-topic by construction —
    # a Google News / GDELT / Reddit / YouTube / Wikipedia / X result returned
    # for a "John Mbadi" query is about John Mbadi. Auto-linking them (instead of
    # gambling on an LLM yes/no that can wrongly drop the FEW items we get when
    # data is thin) is the difference between a real report and "no data yet".
    _TARGETED_SOURCES = {"google_news_rss", "gdelt", "reddit", "youtube", "wikipedia", "scweet", "twscrape"}

    def _needs_llm_link(mention: RawMention) -> bool:
        src = (mention.raw_payload or {}).get("source")
        if not isinstance(src, str):
            src = ""  # some feeds nest `source` as a dict; never hash a dict
        return not (src in _TARGETED_SOURCES and mention.source_type != "comment")

    # Linking decides whether a mention is analysed AT ALL, so it must not be
    # capped wholesale — an unlinked mention is indistinguishable from evidence
    # that was never collected. For targeted sources linking is free (no model
    # call), so every one of those is linked, however many there are. Only the
    # mentions that genuinely need an LLM adjudication are bounded, and the
    # remainder carry over to the next incremental run.
    llm_link_needed = [m for m in unchecked if _needs_llm_link(m)]
    if len(llm_link_needed) > _llm_cap:
        keep = {m.id for m in sorted(llm_link_needed, key=_engagement, reverse=True)[:_llm_cap]}
        unchecked = [m for m in unchecked if not _needs_llm_link(m) or m.id in keep]

    # People extraction is enrichment rather than gating, so it is bounded
    # independently of linking.
    people_budget = {
        m.id for m in sorted(unchecked, key=_engagement, reverse=True)[:_llm_cap]
    }

    def link_only(mention: RawMention) -> dict | None:
        if not _needs_llm_link(mention):
            src = (mention.raw_payload or {}).get("source")
            return {"matched": True, "match_type": f"targeted_{src}", "confidence": 0.75}

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
        return link

    with ThreadPoolExecutor(max_workers=_per_mention_workers()) as pool:
        link_results = list(pool.map(link_only, unchecked))

    # People extraction, batched over the whole set in one pass rather than a
    # round-trip per mention. This was the last unbatched per-item stage left,
    # and at a few hundred mentions it was most of the wall-clock of a report:
    # on a rate-limited backend, ~300 serialised calls is the difference
    # between a report that finishes and one that times out.
    linked_for_people = [
        (m.id, m.text) for m, link in zip(unchecked, link_results)
        if link and m.id in people_budget
    ]
    people_by_mention = entities.extract_people_items(linked_for_people, politician.name)

    for mention, link in zip(unchecked, link_results):
        mention.link_checked = 1
        if not link:
            continue
        people = people_by_mention.get(mention.id, [])
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
    # Score EVERY unscored mention. Batching (~25 items per call on the bulk
    # model) is what makes that affordable — the old one-call-per-mention path
    # forced a top-N cap that left the long tail unread, and an unread tail is
    # indistinguishable from evidence that doesn't exist.
    from engine.agents import score as score_agent
    from engine.db.models import MentionClassification

    # The scoring stage reports its own coverage. A run that scored 0 of 109
    # used to be indistinguishable from a genuinely neutral subject, and the
    # report presented it as a sentiment reading either way.
    scoring_report: dict = {}
    scores = score_agent.score_items(
        politician.name, [(m.id, m.text or "") for m in needing_sentiment],
        report=scoring_report,
    )
    for mention in needing_sentiment:
        scored = scores.get(mention.id)
        if scored is None:
            continue  # unscored: a later run retries rather than guessing
        db.add(
            MentionSentiment(
                mention_id=mention.id,
                sentiment=scored["sentiment"],
                intensity=scored["intensity"],
                context_tag=scored.get("context_tag"),
                confidence=scored["confidence"],
                source=scored["source"],
            )
        )
        # The same pass yields topic/language, so classification is free.
        if scored.get("topic") or scored.get("language"):
            db.add(
                MentionClassification(
                    mention_id=mention.id,
                    topic=scored.get("topic"),
                    language=scored.get("language"),
                    confidence=scored["confidence"],
                )
            )
    db.flush()

    stored_mentions = _as_corpus_dicts(linked_mentions)

    sentiment_rows = (
        db.query(MentionSentiment).filter(MentionSentiment.mention_id.in_([m["id"] for m in stored_mentions])).all()
    )
    sentiments_by_mention = {
        row.mention_id: {"sentiment": row.sentiment, "intensity": row.intensity} for row in sentiment_rows
    }

    # The subject's own name distinguishes nothing inside their own corpus, so
    # it is excluded from any label derived from cluster text.
    subject_terms = {w.lower() for w in (politician.name or "").split() if len(w) > 2}
    subject_terms.update(w.lower() for a in (politician.aliases or []) for w in str(a).split() if len(w) > 2)
    built_narratives = narrative_module.build_narratives(stored_mentions, subject_terms=subject_terms)
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
    # The client dashboard is built on movement between periods, not a single
    # snapshot. Pure arithmetic over data already held, so it never costs a
    # model call.
    try:
        from engine.reports.periods import build_period_series

        payload["period_series"] = build_period_series(
            stored_mentions, sentiments_by_mention, built_narratives,
            (network_snapshot.get("top_people") or []),
            window_start, window_end,
        )
        publish("period_series", payload["period_series"])
    except Exception as exc:  # noqa: BLE001 — charts must never break a report
        stages.current().failed("period_series", exc)
        traceback.print_exc()

    if scoring_report:
        payload["sentiment_scoring"] = scoring_report
        publish("sentiment_scoring", scoring_report)

    # The rule-based payload is complete here — sentiment, volume, narratives,
    # influence and the network are all computed. That is the entire overview,
    # available minutes before the first analyst returns. Publish it.
    for _key, _value in payload.items():
        publish(_key, _value)
    # Discovered documents (full-text articles/archived pages) are evidence too.
    # They feed the DEEP-READING layer — map-reduce digest, analysts, grounding —
    # where a fact buried mid-article can surface. They are deliberately kept out
    # of generate_report_payload above so mention-volume and per-mention
    # sentiment statistics keep their existing meaning.
    # Gate discovered documents before they are read. Discovery is deliberately
    # broad, so this is what stops a same-named person/company/acronym from
    # contaminating the conclusions. Verdicts are stored, so it runs once per
    # document and a re-run resumes rather than re-paying.
    try:
        from engine.agents import disambiguate

        gate_stats = disambiguate.gate_documents(db, politician)
    except Exception as exc:  # noqa: BLE001 — gating must never break a report
        stages.current().failed("relevance_gate", exc)
        gate_stats = {"error": "disambiguation gate failed; documents kept unfiltered"}

    corpus = stored_mentions + _document_corpus(db, politician, window_start, window_end)

    # Everything that needs NO model call is published BEFORE the analyst
    # fan-out, not after it.
    #
    # These four sections are arithmetic over the ingestion stats and two
    # database queries. They sat below enrich_report_payload, so they waited on
    # the slowest LLM stage in the system — and when an analyst was slow or
    # hung, "Since your last report", "Sentiment over time" and "Where this
    # data came from" never arrived at all, despite costing nothing and
    # depending on nothing the analysts produce.
    if ingestion_run is not None:
        payload["data_provenance"] = {
            "ingestion_run_id": ingestion_run.id,
            "status": ingestion_run.status,
            "credits_spent": ingestion_run.credits_spent,
            **(ingestion_run.stats or {}),
        }
        payload["data_coverage"] = _coverage_summary(ingestion_run.stats or {})
        publish("data_provenance", payload["data_provenance"])
        publish("data_coverage", payload["data_coverage"])

    # What the disambiguation gate did. Filtering that nobody can see is
    # indistinguishable from data loss, so the counts travel with the report.
    payload["evidence_gate"] = gate_stats
    publish("evidence_gate", gate_stats)

    # Report-over-report change tracking (computed before this report is
    # stored, so "previous" is genuinely the prior report).
    from engine.reports.deltas import compute_deltas, sentiment_history

    deltas = compute_deltas(db, politician, payload)
    if deltas:
        payload["since_last_report"] = deltas
    payload["sentiment_history"] = sentiment_history(db, politician)
    publish("since_last_report", payload.get("since_last_report"))
    publish("sentiment_history", payload["sentiment_history"])

    # And again before the long stretch, so anything that failed during
    # scoring, linking or narratives is on the page while the analysts run.
    publish("section_status", stages.current().summary())
    publish("run_health", health.current().summary())

    payload = enrich_report_payload(
        politician.name,
        window_start,
        window_end,
        payload,
        mentions=corpus,
        narratives=built_narratives,
        on_section=publish,
    )

    # Resolve the corpus into entities and events: many reports of one happening
    # become ONE event carrying its evidence, so repetition stops masquerading
    # as significance. Runs on the gated corpus and is idempotent across runs.
    if settings.enable_resolution:
        try:
            from engine.agents import resolve as resolve_agent

            payload["resolution"] = resolve_agent.resolve_corpus(db, politician, corpus)

            # Score the sources behind that evidence. Runs after resolution
            # because corroboration is measured through shared events, and it
            # must precede verification so claim confidence reflects source
            # quality rather than raw count.
            from engine.agents import credibility as credibility_agent

            payload["source_credibility"] = credibility_agent.score_sources(db, politician)

            # Extend the knowledge graph. It persists between runs, so each
            # report leaves the picture of the subject's world richer than it
            # found it — connections are what single documents can't show.
            from engine.agents import knowledge_graph as kg_agent

            payload["knowledge_graph"] = kg_agent.build_graph(db, politician, corpus)

            # What moved since last time, and what deserves attention before it
            # becomes obvious. Both run after resolution/graph so they can see
            # events, entities and relationships — the things that change.
            from engine.agents import anomaly as anomaly_agent
            from engine.agents import temporal as temporal_agent

            payload["temporal"] = temporal_agent.temporal_summary(
                db, politician, run_id=ingestion_run.id if ingestion_run else None
            )
            payload["signals"] = anomaly_agent.detect_all(db, politician)
            for _key in ("resolution", "source_credibility", "knowledge_graph", "temporal", "signals"):
                publish(_key, payload.get(_key))
        except Exception as exc:  # noqa: BLE001 — resolution must never break a report
            stages.current().failed("entity_event_resolution", exc)
            traceback.print_exc()
            payload["resolution"] = {"error": "entity/event resolution failed"}


    # Client-facing deliverable, shaped to the Sentiment Analysis Framework
    # V1.0 exactly — same parameter numbering, ordering and terminology, so an
    # analyst reads their own structure rather than our interpretation of it.
    try:
        from engine.reports import sentiment_framework

        previous_report = (
            db.query(IntelligenceReport)
            .filter(IntelligenceReport.politician_id == politician.id)
            .order_by(IntelligenceReport.generated_at.desc())
            .first()
        )
        payload["sentiment_framework"] = sentiment_framework.build(
            politician,
            payload,
            corpus,
            previous=(previous_report.payload if previous_report else None),
            sentiments=sentiments_by_mention,
        )
        publish("sentiment_framework", payload["sentiment_framework"])
    except Exception as exc:  # noqa: BLE001 — the framework view must not break a report
        # The client deliverable. Silently absent, this tab simply never
        # appeared and nobody could tell whether it was empty or broken.
        stages.current().failed("sentiment_framework", exc)
        traceback.print_exc()

    # The run's own account of whether the model answered. Stamped onto the
    # payload so a reader is never shown empty sections without being told the
    # backend was down, and so a broken run cannot be presented as an analysis.
    payload["run_health"] = health.current().summary()
    # Which sections were produced, which found nothing, and which failed
    # trying. Without this a failed section is indistinguishable from a section
    # whose subject was simply quiet — the defect behind four separate bugs.
    payload["section_status"] = stages.current().summary()
    publish("run_health", payload["run_health"])
    publish("section_status", payload["section_status"])

    report = IntelligenceReport(
        politician_id=politician.id,
        period=period,
        window_start=window_start,
        window_end=window_end,
        payload=payload,
    )
    db.add(report)
    db.commit()

    # Audit the finished narrative against the stored corpus. This runs LAST, on
    # what the report actually says, and records a status + citations for every
    # factual claim. A claim the evidence can't support is labelled, not deleted:
    # a thin spot in the file is itself a finding worth seeing.
    if settings.enable_verification:
        try:
            from engine.agents import verify

            audit = verify.verify_payload(db, politician, payload, report_id=report.id)
            payload["verification"] = {
                k: audit[k] for k in ("checked", "verified", "unverified", "contradicted")
            }
            payload["claims"] = audit["claims"]
            publish("verification", payload["verification"])
            publish("claims", payload["claims"])

            # Finally, refuse to call the file finished: name what is missing and
            # turn those gaps into concrete queries for the NEXT run. This runs
            # after verification because unverified claims are the richest source
            # of "what still needs establishing".
            if settings.enable_investigator:
                from engine.agents import investigator

                agenda = investigator.build_agenda(db, politician)
                investigator.store_follow_up_queries(
                    db, politician, agenda.get("follow_up_queries") or []
                )
                payload["investigation"] = agenda

            report.payload = payload
            flag_modified(report, "payload")
            db.commit()
        except Exception as exc:  # noqa: BLE001 — an audit failure must not lose the report
            stages.current().failed("verification", exc)
            traceback.print_exc()

    return report


def _coverage_summary(stats: dict) -> dict:
    """Human-readable data-coverage disclosure for the report: which sources
    delivered, which failed and why. A thin report must say why it is thin."""
    health = stats.get("source_health") or {}
    ok, degraded, down = [], [], []
    notes = []
    for source, h in sorted(health.items()):
        failures = h.get("failures") or {}
        # A source whose tasks all completed but which returned nothing AND
        # recorded a reason has not delivered. "succeeded == attempted" was
        # true for a connector the host refused, so a run where Reddit and X
        # were blocked still printed "Every enabled source delivered".
        silently_empty = bool(failures.get("silent_empty"))
        delivered = h.get("results", 0) > 0
        if silently_empty and not delivered:
            down.append(source)
        elif h.get("succeeded", 0) == h.get("attempted", 0) and h.get("attempted", 0) > 0:
            (ok if delivered or not silently_empty else degraded).append(source)
        elif h.get("succeeded", 0) > 0:
            degraded.append(source)
        else:
            down.append(source)
        if failures.get("out_of_credits"):
            notes.append(f"{source}: data provider credits exhausted — top up to restore coverage")
        elif failures.get("endpoint_unavailable"):
            notes.append(f"{source}: provider endpoint unavailable this run")
        elif failures.get("upstream_error"):
            notes.append(f"{source}: partial — upstream errors during collection")
        elif failures.get("silent_empty"):
            raw = [e.replace("returned nothing: ", "") for e in (h.get("errors") or [])[:2]]
            reason = "; ".join(raw) or "no reason recorded"
            notes.append(f"{source}: returned nothing — {reason}")
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
    consecutive_failures = 0
    for video in sorted(videos, key=engagement_score, reverse=True):
        if fetched >= settings.transcripts_per_run:
            break
        # Stop asking after three refusals in a row. A wrong parameter name or
        # an exhausted plan fails identically for every video, and paying the
        # latency of that discovery once per video — on a premium endpoint —
        # buys nothing.
        if consecutive_failures >= 3:
            stages.current().failed(
                "transcripts",
                f"stopped after {consecutive_failures} consecutive failures: "
                f"{getattr(connector, 'last_error', 'no reason recorded')}")
            break
        payload = video.raw_payload or {}
        post_id = payload.get("id") or payload.get("video_id") or payload.get("post_id")
        if not post_id or (video.platform, str(post_id)) in existing_transcripts:
            continue
        try:
            transcript = connector.fetch_transcript(video.platform, str(post_id))
        except Exception as exc:  # noqa: BLE001 — one video is not the run
            # Transcripts are the only way the words spoken in a 90-minute
            # broadcast enter the corpus at all. Losing them all silently reads
            # as coverage that happened to be text-only.
            stages.current().failed(f"transcript:{video.platform}", exc)
            consecutive_failures += 1
            continue
        if transcript is None:
            consecutive_failures += 1
            continue
        consecutive_failures = 0
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

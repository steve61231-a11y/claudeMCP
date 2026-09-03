"""Issue mapping — the intersection of a PRINCIPAL and an ISSUE/INSTITUTION.

"President William Ruto" × "forestry". "John Mbadi" × "SHA". A person × "KRA".
Instead of a general report about one subject, this builds a focused corpus at
the *intersection* of two terms — only material that mentions BOTH — then digests
and analyses it into a map of how the principal is actually connected to the
issue: their involvement, the linking narratives, the key actors, a timeline and
where they're exposed.

Acquisition combines both terms with AND semantics across the keyless sources
(GDELT full-text news, NewsAPI when keyed), enriches article bodies, and runs
the same whole-corpus map-reduce digest used everywhere else — so the analyst
provably reads every intersection mention, not a truncated slice.

Sandbox note: external egress is blocked here, so acquisition returns [] and the
map degrades gracefully; it lights up on deploy. Callers may inject `mentions`
directly (used by tests and by callers that already hold an intersection corpus).
"""

import traceback
from datetime import datetime, timedelta

from engine import stages
from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention
from engine.ingestion.gdelt_connector import GdeltConnector
from engine.reports import decompose, issue_graph, relevance

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_MAX = 250


def _gdelt_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """GDELT DOC full-text search requiring BOTH terms (AND is implicit when
    space-separated quoted phrases are given)."""
    query = f'"{principal}" "{issue}"'
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(_GDELT_MAX),
        "sort": "datedesc",
        "startdatetime": ws.strftime("%Y%m%d%H%M%S"),
        "enddatetime": we.strftime("%Y%m%d%H%M%S"),
    }
    try:
        resp = http.get(GDELT_DOC_URL, params=params, timeout=30)
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("issue_map:gdelt_intersection", exc)
        return []

    out: list[IngestedMention] = []
    seen: set[str] = set()
    for art in body.get("articles") or []:
        url = art.get("url")
        title = (art.get("title") or "").strip()
        if not url or url in seen or not title:
            continue
        seen.add(url)
        # Every article used to be stamped with the window end, which made the
        # whole issue-map timeline fictional — dozens of items "happening" on
        # the same day, and the analyst dating events from it. GDELT ships the
        # real timestamp in `seendate`; parse it the way the main connector
        # does and clamp out-of-window items rather than inventing a date.
        posted = GdeltConnector._parse_seendate(art.get("seendate")) or we
        posted = min(max(posted, ws), we)
        out.append(
            IngestedMention(
                platform=art.get("domain") or "news",
                source_type="article",
                author_handle=art.get("domain") or "news",
                text=title,
                posted_at=posted,
                engagement={},
                raw_payload={"url": url, "title": title, "source": "gdelt", "intersection": True},
            )
        )
    return out


def _both_terms_for_quoting_source(identity: str, issue_term: str) -> str:
    """A both-terms query for a connector that quotes the name it is given.

    GoogleNewsRssConnector wraps the subject name in quotes and ORs the aliases
    in, so passing `Ruto" "SHA` produces `"Ruto" "SHA"` — two quoted phrases
    side by side, which the engine reads as AND. It is a shim around a
    connector interface built for one subject, kept in one named place rather
    than repeated at each call site.
    """
    return f'{identity}" "{issue_term}'


def _google_news_intersection(identity: str, issue_term: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """Google News RSS requiring BOTH terms — the strongest free source for the
    Kenyan-politics intersection (local outlets GDELT misses)."""
    from engine.ingestion.google_news_rss_connector import GoogleNewsRssConnector

    return GoogleNewsRssConnector().fetch(
        _both_terms_for_quoting_source(identity, issue_term), [], ws, we
    )


def _reddit_intersection(identity: str, issue_term: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """Reddit search takes the query verbatim; quoted phrases separated by a
    space are an AND of both phrases."""
    from engine.ingestion.reddit_connector import RedditConnector

    return RedditConnector().fetch(f'"{identity}" "{issue_term}"', [], ws, we)


def _youtube_intersection(identity: str, issue_term: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    from engine.ingestion.youtube_connector import YouTubeConnector

    return YouTubeConnector().fetch(f"{identity} {issue_term}", [], ws, we)


# How many identity x issue-name pairs each source is asked for. GDELT and
# Google News are cheap keyless requests and carry the news record, so they get
# the widest sweep; YouTube costs a yt-dlp subprocess per query, so it gets the
# fewest. One literal query per source is what made the issue map shallow —
# these numbers are the fix, and they are here to be tuned rather than buried.
PAIR_BUDGET = {"gdelt": 8, "google_news": 8, "reddit": 4, "youtube": 3}


def _pairs(identities: list[str], issue_terms: list[str], budget: int) -> list[tuple[str, str]]:
    """Identity x issue-name pairs, most-specific first.

    Breadth-first over identities so a small budget still covers the primary
    name against every way the issue is written, before spending anything on
    the second alias.
    """
    out: list[tuple[str, str]] = []
    for term in issue_terms:
        for identity in identities:
            out.append((identity, term))
    out.sort(key=lambda pair: (identities.index(pair[0]) + issue_terms.index(pair[1])))
    return out[:budget]


def acquire_intersection(
    principal: str,
    issue: str,
    ws: datetime,
    we: datetime,
    identities: list[str] | None = None,
    issue_terms: list[str] | None = None,
) -> list[IngestedMention]:
    """Gather mentions that connect the principal and the issue.

    This used to be four requests: one literal AND-query to each of three
    sources. That is why an issue map came back with four actors — it was
    analysing four requests' worth of material while the politician path fanned
    out across dozens of identity variants and eighty discovery probes. Here it
    sweeps every identity variant against every way the issue is named, across
    every enabled free source, and enriches article bodies so the digest reads
    whole journalism rather than headlines.
    """
    identities = [i for i in (identities or [principal]) if i] or [principal]
    issue_terms = [t for t in (issue_terms or [issue]) if t] or [issue]

    mentions: list[IngestedMention] = []
    seen: set[str] = set()

    def _add(items):
        for m in items or []:
            key = (m.get("raw_payload") or {}).get("url") or (m.get("text") or "")[:80]
            if key and key not in seen:
                seen.add(key)
                mentions.append(m)

    def _sweep(source: str, fetch):
        for identity, term in _pairs(identities, issue_terms, PAIR_BUDGET[source]):
            try:
                _add(fetch(identity, term, ws, we))
            except Exception as exc:  # noqa: BLE001 — one bad query must not end the sweep
                stages.current().failed(f"issue_map:sweep:{source}", exc)
                continue

    if settings.enable_gdelt:
        _sweep("gdelt", _gdelt_intersection)
    if settings.enable_google_news:
        _sweep("google_news", _google_news_intersection)
    if settings.enable_reddit:
        _sweep("reddit", _reddit_intersection)
    if settings.enable_youtube:
        _sweep("youtube", _youtube_intersection)

    if mentions:
        from engine.ingestion.article_text import enrich_with_article_text

        enrich_with_article_text(mentions)
    return mentions


def acquire_intersection_documents(discovery_queries: list[str],
                                   subject_name: str = "",
                                   aliases: list[str] | None = None,
                                   report: dict | None = None) -> list[dict]:
    """Full-page documents at the intersection, via metasearch discovery.

    This is the layer that reaches the material no fixed connector indexes —
    committee reports, county statements, court filings, archived pages — and
    it is the single biggest source of depth available to an issue map. The
    issue map never used it.
    """
    # Discovery failing is not the same as discovery finding nothing, and the
    # two were indistinguishable from outside: a dead SearXNG returned [] and
    # the run carried on reporting a thin corpus as if the sweep had run and
    # come back empty. `report` is filled in either way and travels with the
    # map, so a broken instance is visible instead of inferred.
    diagnostics = report if report is not None else {}
    diagnostics.update({
        "enabled": bool(settings.enable_discovery),
        "configured": bool(settings.searxng_url),
        "queries": len(discovery_queries or []),
        "documents": 0, "error": None, "skipped": None,
    })
    if not settings.enable_discovery:
        diagnostics["skipped"] = "discovery is switched off"
        return []
    if not settings.searxng_url:
        diagnostics["skipped"] = "SEARXNG_URL is not set — the deepest source of material is unreachable"
        stages.current().failed("issue_map:discovery", diagnostics["skipped"])
        return []
    if not discovery_queries:
        diagnostics["skipped"] = "no queries to sweep"
        return []
    try:
        from engine.ingestion.discovery_connector import DiscoveryConnector

        # The subject is the PRINCIPAL, not the first query string. Passing the
        # query meant the on-topic check ran against the literal text
        # `"Okiya Omtatah" "International Monetary Fund"` — quote marks and all
        # — which appears in no page on earth, and the surname it derived was
        # `fund"`. Every discovered document failed the check and the single
        # richest source of depth returned nothing on every run it ever made.
        connector = DiscoveryConnector()
        documents = connector.fetch_documents(
            subject_name or discovery_queries[0], list(aliases or []), discovery_queries
        )
        diagnostics["documents"] = len(documents)
        last_error = getattr(connector, "last_error", None)
        diagnostics["error"] = last_error
        if last_error:
            stages.current().failed("issue_map:discovery", last_error)
        elif not documents:
            stages.current().empty("issue_map:discovery",
                                   f"{len(discovery_queries)} queries returned no usable pages")
        else:
            stages.current().ok("issue_map:discovery", f"{len(documents)} documents")
        return documents
    except Exception as exc:  # noqa: BLE001 — discovery is additive, never required
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"[:200]
        stages.current().failed("issue_map:discovery", exc)
        return []


def _ensure_subject(db, principal: str):
    """The subject row the intersection corpus is stored under.

    Deliberately the PRINCIPAL, not "principal x issue": material about Ruto
    and SHA is material about Ruto. Storing it under the person means an issue
    map enriches the corpus a politician report reads, a politician report
    enriches what the next issue map retrieves, and a second issue map on the
    same person starts from everything the first one found.
    """
    from engine.db.models import Politician

    subject = db.query(Politician).filter_by(name=principal).first()
    if subject is None:
        subject = Politician(name=principal, aliases=[principal], keywords=[])
        db.add(subject)
        db.commit()
    return subject


def _persist_intersection(db, subject, mentions, documents, issue: str) -> dict:
    """Store the intersection corpus under the subject, with provenance.

    The issue map used to keep nothing at all: every run re-scraped from zero,
    could never compound, and could not reuse a corpus the politician path had
    already built for the same person. Storage is idempotent (content-hash
    upserts), so re-running an issue map costs requests, never duplicates.
    """
    from engine.db.models import IngestionRun, IngestionTask
    from engine.ingestion import orchestrator

    run = IngestionRun(
        politician_id=subject.id,
        window_start=datetime.utcnow() - timedelta(days=365),
        window_end=datetime.utcnow(),
        status="running",
        credit_budget=0.0,
        stats={"kind": "issue_map", "issue": issue},
    )
    db.add(run)
    db.flush()
    task = IngestionTask(
        run_id=run.id, connector="issue_map", platform="mixed",
        endpoint="intersection", query=f"{subject.name} {issue}",
    )
    db.add(task)
    db.commit()

    stored_mentions = 0
    stored_documents = 0
    try:
        stored_mentions = orchestrator._store_mentions(db, run, task, subject, mentions)
    except Exception:  # noqa: BLE001 — a storage failure must not lose the map
        traceback.print_exc()
    try:
        stored_documents = orchestrator._store_documents(db, run, subject, documents)
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    run.status = "complete"
    db.commit()
    return {"run_id": run.id, "mentions_stored": stored_mentions,
            "documents_stored": stored_documents}


def _gate_documents(db, subject) -> dict:
    """Discovery is deliberately broad, so a same-named person, company or
    acronym can otherwise walk straight into the conclusions — the exact
    failure this gate exists to prevent, and one the issue map skipped."""
    try:
        from engine.agents import disambiguate

        return disambiguate.gate_documents(db, subject)
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("issue_map:relevance_gate", exc)
        return {"error": "disambiguation gate failed; documents kept unfiltered"}


def _resolve_intersection(db, subject, corpus: list[dict]) -> dict:
    """Entity and event resolution over the intersection corpus.

    Many reports of one happening become ONE event carrying its evidence, so
    repetition stops masquerading as significance — and the entities and
    relationships persist, which is what lets the next run start further ahead.
    """
    if not settings.enable_resolution or not corpus:
        return {}
    try:
        from engine.agents import resolve as resolve_agent

        return resolve_agent.resolve_corpus(db, subject, corpus)
    except Exception:  # noqa: BLE001 — resolution must never break a map
        traceback.print_exc()
        return {"error": "entity/event resolution failed"}


def build_issue_map(
    principal: str,
    issue: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    mentions: list[dict] | None = None,
    desired_outcome: str | None = None,
    issue_aliases: list[str] | None = None,
    on_section=None,
) -> dict:
    """Produce the issue-map payload for principal × issue.

    Acquires (or accepts injected) intersection material across every enabled
    free source with full identity x issue-name expansion plus metasearch
    discovery, stores it under the principal so the corpus compounds, pulls
    back everything already stored that bears on the issue, gates and resolves
    it, then runs the whole-corpus map-reduce digest and the intersection
    analyst. Returns a self-describing payload including a coverage record and
    an evidence sample.

    `mentions` short-circuits acquisition entirely (tests, and callers that
    already hold an intersection corpus). `on_section(key, value)` streams
    stages to a waiting reader, same contract as run_analysis.
    """
    from engine.reports import analysts
    from engine.reports.digest import build_corpus_digest

    def publish(key, value):
        if on_section is None:
            return
        try:
            on_section(key, value)
        except Exception:  # noqa: BLE001
            pass

    we = window_end or datetime.utcnow()
    ws = window_start or (we - timedelta(days=365))
    acquisition: dict = {}

    if mentions is None:
        mentions, acquisition = _acquire_and_store(principal, issue, ws, we,
                                                   issue_aliases, publish)

    label = f"{principal} × {issue}"

    # Stop before spending twenty minutes and a corpus of model calls on
    # material that cannot answer the question. A run that reads 370 documents
    # about the wrong country and returns empty sections is worse than one that
    # says in ten seconds that it found nothing on topic: the first looks like
    # the subject has no story, the second tells you the search was wrong.
    # Stop before the digest only when there is genuinely nothing to read. Two
    # cases, and they are not the same:
    #
    #   - the corpus is empty. Nothing to analyse, at any price.
    #   - we collected plenty and the relevance filter rejected nearly all of
    #     it. This is "senate × forestry": 370 documents, one on topic. Reading
    #     them costs twenty minutes and produces empty sections.
    #
    # A small corpus is NOT one of them. Two genuinely on-topic articles are
    # thin, not useless, and they cost seconds to read — refusing to analyse
    # them would withhold the only answer available.
    filtered = acquisition.get("relevance_filter") or {}
    examined, kept = filtered.get("examined"), filtered.get("kept")
    nothing_at_all = not mentions
    filter_rejected_nearly_everything = (
        examined is not None and examined >= REJECTION_SAMPLE_FLOOR
        and kept is not None and kept < MIN_USABLE_DOCUMENTS
    )
    if nothing_at_all or filter_rejected_nearly_everything:
        report = filtered or {
            "examined": len(mentions), "kept": len(mentions), "dropped": 0,
            "reasons": {}, "examples": {},
            "market_anchored": relevance.needs_market_anchor(principal, issue),
        }
        publish("stage", "Nothing on topic — stopping before analysis.")
        return _nothing_on_topic(principal, issue, ws, we, mentions, acquisition, report)

    # How many of these actually name both halves. A map built entirely from
    # background is a real result — "the public record does not connect these
    # two" — but it must not be presented as if it were the same as a map built
    # from documents that do.
    pool_counts = (filtered.get("pools") or {})
    core_count = pool_counts.get(relevance.POOL_CORE)
    if core_count is not None:
        publish("stage",
                f"Reading {len(mentions)} sources — {core_count} name both, "
                f"{len(mentions) - core_count} give the background around them…")
    else:
        publish("stage", f"Reading {len(mentions)} intersection sources…")
    digest = build_corpus_digest(label, mentions)
    publish("coverage", digest["coverage"])
    # Four analysts run at once; each publishes the map as it stands the moment
    # its section lands, so the reader gets the actors while the timeline is
    # still being written.
    analysis = analysts.analyze_issue_intersection(
        principal, issue, digest,
        on_part=lambda name, partial: publish("intersection", partial),
    )
    publish("intersection", analysis)

    sample = [
        {
            "platform": m.get("platform"),
            "text": (m.get("text") or "")[:400],
            "url": (m.get("raw_payload") or {}).get("url") or m.get("source_url"),
            "posted_at": m.get("posted_at"),
        }
        for m in mentions[:15]
    ]

    payload = {
        "principal": principal,
        "issue": issue,
        "window": {"start": ws, "end": we},
        "generated_at": datetime.utcnow(),
        "coverage": digest["coverage"],
        "intersection": analysis,
        "evidence_sample": sample,
        "thin": digest["coverage"]["mentions_total"] == 0,
    }
    if core_count == 0 and mentions:
        # Said once, plainly, rather than left for the reader to infer from
        # sections that quietly have nothing in them.
        payload["intersection_gap"] = {
            "read": len(mentions),
            "note": (f"No document in this window names both “{principal}” and "
                     f"“{issue}”. Everything below is each side's own record and "
                     f"the actors around it — treat any connection between them "
                     f"as unestablished until a source says otherwise."),
        }
    if acquisition:
        # How the corpus was assembled travels with the map. A thin result has
        # to be distinguishable from a collection failure.
        payload["acquisition"] = acquisition

    # Draw the graph as soon as the intersection lands, before the framework —
    # which is the slowest call in the run. It costs nothing but local work, and
    # it means the reader gets the map minutes earlier instead of watching a
    # stage line. It is rebuilt below once the framework's contours exist.
    _publish_graph(principal, issue, analysis, None, mentions, payload, publish)

    payload["issue_framework"] = _issue_framework(principal, issue, payload, analysis,
                                                  desired_outcome=desired_outcome)
    publish("issue_framework", payload["issue_framework"])

    # One graph over everything the investigation found, built from the SAME
    # objects the sections are rendered from — so selecting a node can filter
    # the timeline, the actors and the evidence to the same underlying items.
    # A separate, prettier graph derived from nothing is the disconnected
    # spiderweb this is explicitly not.
    _publish_graph(principal, issue, analysis, payload["issue_framework"],
                   mentions, payload, publish)
    return payload


#: How much of each pool an analyst reads. The intersection is the answer, so
#: it is never crowded out; the two background pools exist to give the analyst
#: enough of the world for the intersection to mean something, and to be the
#: place buried older context is found.
POOL_BUDGET = {
    "core": 400,
    "principal_side": 220,
    "issue_side": 180,
}


def _blend_pools(pools: dict) -> list[dict]:
    """One corpus, intersection first, each item still carrying which pool it
    came from so the analyst is never guessing what a document is evidence of."""
    def newest(items):
        return sorted(items, key=lambda d: str(d.get("posted_at") or ""), reverse=True)

    blended: list[dict] = []
    for name, budget in POOL_BUDGET.items():
        blended.extend(newest(pools.get(name) or [])[:budget])
    return blended


def _publish_graph(principal, issue, analysis, framework, mentions, payload, publish):
    """Build the graph and hand it to the reader. Never at the cost of the map:
    a view that fails is a missing view, not a failed investigation."""
    try:
        payload["issue_graph"] = issue_graph.build(
            principal, issue, analysis, framework, mentions)
        publish("issue_graph", payload["issue_graph"])
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("issue_graph", exc)


#: Below this many on-topic documents, a corpus that was heavily filtered has
#: nothing left worth the digest.
MIN_USABLE_DOCUMENTS = 3

#: Only call it "nothing on topic" when enough was collected for the filter's
#: verdict to mean something. Rejecting 2 of 2 says little; rejecting 369 of
#: 370 says the search was wrong.
REJECTION_SAMPLE_FLOOR = 20


def _nothing_on_topic(principal: str, issue: str, ws, we, mentions: list[dict],
                      acquisition: dict, report: dict) -> dict:
    """A truthful empty result, delivered immediately, saying what to change."""
    reasons = report.get("reasons") or {}
    top = sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)
    detail = "; ".join(f"{count} {reason}" for reason, count in top[:3])
    guidance = []
    if report.get("market_anchored"):
        guidance.append(
            f"“{principal}” and “{issue}” are generic terms, so the search was anchored to "
            "Kenya. Naming the specific body or person — “Senate Committee on Lands”, "
            "“Kenya Forest Service”, a named senator — will find the intersection where "
            "the generic pair cannot.")
    if any("principal" in r for r in reasons):
        guidance.append(f"Nothing collected mentioned “{principal}” at all.")
    if any("issue" in r for r in reasons):
        guidance.append(f"Nothing collected mentioned “{issue}” at all.")
    return {
        "principal": principal,
        "issue": issue,
        "window": {"start": ws, "end": we},
        "generated_at": datetime.utcnow(),
        "coverage": {"mentions_total": len(mentions), "mentions_analyzed": 0,
                     "chunks": 0, "complete": False,
                     "note": f"{report['examined']} documents were collected and none were "
                             "about this intersection, so no analysis was run."},
        "intersection": {},
        "evidence_sample": [],
        "thin": True,
        "nothing_on_topic": {
            "examined": report.get("examined", 0),
            "kept": report.get("kept", 0),
            "why": detail or "no documents matched both terms",
            "examples": report.get("examples") or {},
            "guidance": guidance,
        },
        "acquisition": acquisition,
        "issue_framework": None,
    }


def _merge_queries(*groups) -> list[str]:
    """Every query, in order, without repeats. Case-folded so `"IMF" "Omtatah"`
    and `"imf" "omtatah"` do not both cost a sweep."""
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for query in group or []:
            key = " ".join(str(query).lower().split())
            if key and key not in seen:
                seen.add(key)
                merged.append(query)
    return merged


def _acquire_and_store(principal: str, issue: str, ws: datetime, we: datetime,
                       issue_aliases: list[str] | None,
                       publish) -> tuple[list[dict], dict]:
    """Acquire the intersection, persist it, and read back everything stored
    that bears on the issue — including whatever earlier runs already found.

    Falls back to acquisition-only (no store, no reuse) if the database is
    unreachable, so an issue map still works where a report couldn't.
    """
    from engine.db.session import SessionLocal
    from engine.ingestion import queries

    db = None
    try:
        db = SessionLocal()
        subject = _ensure_subject(db, principal)
        identities = queries.text_variants(subject)
        issue_terms = queries.issue_variants(issue, issue_aliases)
        discovery = queries.intersection_discovery_variants(subject, issue, issue_aliases)
    except Exception:  # noqa: BLE001 — no database is not a reason to fail
        traceback.print_exc()
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        publish("stage", f"Searching for “{principal}” × “{issue}”…")
        fresh = acquire_intersection(principal, issue, ws, we)
        return _as_corpus(fresh), {"stored": False, "reason": "database unavailable"}

    try:
        # "senate" and "forestry" match every English-speaking legislature on
        # earth. A distinctive name anchors its own search; a generic term does
        # not, and without geography the corpus is the whole world's coverage.
        if relevance.needs_market_anchor(principal, issue):
            discovery = [relevance.anchor_query(q) for q in discovery]
            publish("stage", "Generic terms — anchoring every query to Kenya…")

        # An issue map is not one search. "Okiya Omtatah × IMF" is a question
        # about a person, an institution, a legal case, a history and a set of
        # opponents, and asking a search engine the whole sentence finds none
        # of it. Add a query per research dimension: background on each half,
        # the conflict, the institutions with formal power, and older material
        # that a recency-ranked search buries.
        # Match on the NAMES inside the boxes, not the boxes as typed. A
        # principal of "Odious debt case by Okiya Omtatah" is a claim with a
        # person inside it; demanding the whole phrase discarded an interview
        # headlined "Okiya Omtatah: The Truth Behind Kenya's Debt and the IMF
        # Fall-out" for not mentioning the principal.
        principal_parts = decompose.decompose(principal)
        issue_parts = decompose.decompose(issue)
        match_identities = relevance.merge_terms(identities, principal_parts["identities"])
        match_issue_terms = relevance.merge_terms(issue_terms, issue_parts["identities"])

        dimensions = decompose.research_dimensions(principal, issue)
        dimension_queries = [q for d in dimensions for q in d["queries"]]
        if relevance.needs_market_anchor(principal, issue):
            dimension_queries = [relevance.anchor_query(q) for q in dimension_queries]
        discovery = relevance.merge_terms(discovery, dimension_queries)
        publish("stage", f"{len(dimensions)} research dimensions, "
                         f"{len(discovery)} queries — searching current and historical…")
        publish("research_plan", [
            {"dimension": d["dimension"], "why": d["why"], "queries": d["queries"]}
            for d in dimensions])
        publish("stage", f"Searching {len(identities)} name variants × "
                         f"{len(issue_terms)} issue terms across every source…")
        fresh = acquire_intersection(principal, issue, ws, we,
                                     identities=identities, issue_terms=issue_terms)
        publish("stage", f"Sweeping {len(discovery)} discovery probes for full-text sources…")
        # Match discovered pages against the principal's own names, and against
        # the issue's — a document about the IMF's Kenya programme is evidence
        # for the issue side even when it never names him.
        document_terms = _merge_queries(match_identities, match_issue_terms)
        discovery_report: dict = {}
        documents = acquire_intersection_documents(
            discovery, subject_name=principal, aliases=document_terms,
            report=discovery_report)
        if discovery_report.get("error") or discovery_report.get("skipped"):
            publish("stage", "Discovery sweep unavailable — "
                             + str(discovery_report.get("error")
                                   or discovery_report.get("skipped")))

        stored = _persist_intersection(db, subject, fresh, documents, issue)
        gate = _gate_documents(db, subject)

        publish("stage", "Pulling back everything stored that bears on this issue…")
        from engine.agents import evidence

        corpus = evidence.retrieve_intersection(db, subject.id, issue_terms)
        # Anything acquired this run that the store hasn't indexed yet (or that
        # full-text search ranks out) is still evidence — union, never replace.
        corpus = _merge_corpus(corpus, _as_corpus(fresh))

        # Gate the MERGED corpus, not just the stored half. Freshly acquired
        # items were unioned in after the gate ran, so nothing collected during
        # a run was ever checked — which is how 370 documents of American local
        # news reached the analysts for a Kenyan "senate × forestry" mapping.
        require_market = relevance.needs_market_anchor(principal, issue)
        # Sort into pools rather than gating on a hard AND. Demanding both
        # halves in every document kept TWO articles out of hundreds for
        # "Odious debt case by Okiya Omtatah" × "IMF", and five analysts then
        # spent ten minutes reading two articles. The intersection still needs
        # both; the background around it does not, and without that background
        # there is nothing to find the intersection IN.
        pools, relevance_report = relevance.partition_corpus(
            corpus, match_identities, match_issue_terms, require_market)
        relevance_report["matched_on"] = {
            "principal": match_identities[:6], "issue": match_issue_terms[:6]}
        corpus = _blend_pools(pools)
        counts = relevance_report["pools"]
        publish("stage",
                f"{counts[relevance.POOL_CORE]} documents on the intersection, "
                f"{counts[relevance.POOL_PRINCIPAL]} on {principal_parts['names'][0] if principal_parts['names'] else 'the principal'}, "
                f"{counts[relevance.POOL_ISSUE]} on the issue — "
                f"{relevance_report['dropped']} set aside…")

        resolution = _resolve_intersection(db, subject, corpus)
        return corpus, {
            "stored": True,
            "subject_id": subject.id,
            "identity_variants": len(identities),
            "issue_terms": issue_terms,
            "discovery_probes": len(discovery),
            "discovery": discovery_report,
            "fresh_mentions": len(fresh),
            "fresh_documents": len(documents),
            **stored,
            "evidence_gate": gate,
            "relevance_filter": relevance_report,
            "resolution": {k: v for k, v in (resolution or {}).items() if k != "events"},
            "corpus_from_store": len(corpus),
        }
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        fresh = acquire_intersection(principal, issue, ws, we,
                                     identities=identities, issue_terms=issue_terms)
        return _as_corpus(fresh), {"stored": False, "reason": "acquisition or storage failed"}
    finally:
        try:
            db.close()
        except Exception:
            pass


def _as_corpus(mentions) -> list[dict]:
    """IngestedMention dicts in the shape the digest and analysts read."""
    out: list[dict] = []
    for i, m in enumerate(mentions or []):
        raw = m.get("raw_payload") or {}
        out.append(
            {
                "id": m.get("id") or f"fresh-{i}",
                "platform": m.get("platform"),
                "source_type": m.get("source_type"),
                "author_handle": m.get("author_handle"),
                "text": m.get("text") or "",
                "posted_at": m.get("posted_at"),
                "engagement": m.get("engagement") or {},
                "language": m.get("language"),
                "source_url": raw.get("url") or m.get("source_url"),
            }
        )
    return out


def _merge_corpus(primary: list[dict], extra: list[dict]) -> list[dict]:
    """Union by URL, falling back to the opening of the text.

    The same article arriving from the store and from this run's fetch must not
    be read twice — repetition is exactly what the resolution layer exists to
    stop masquerading as significance.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for item in list(primary) + list(extra):
        key = (item.get("source_url") or "").strip() or (item.get("text") or "")[:120].strip()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _issue_framework(principal: str, issue: str, payload: dict, analysis: dict,
                     desired_outcome: str | None = None) -> dict | None:
    """Render the intersection to the client's Issue Analysis & Mapping Framework.

    The framework is a presentation of what the analysis already found — the
    actors become stakeholders, the intersection timeline becomes the background
    record. Nothing is invented here: an actor with no stated stance stays
    neutral, and an undated moment simply carries no date. A failure to render
    the framework must not cost the caller the issue map itself.
    """
    from engine.reports import issue_framework as ifw

    try:
        stakeholders = []
        for actor in analysis.get("key_actors") or []:
            name = (actor.get("name") or "").strip()
            if not name:
                continue
            position = actor.get("position")
            stakeholders.append({
                "name": name,
                # Same exact-match defect fixed in issue_framework: a model
                # writing "For" or "supportive" was silently reclassified as
                # neutral, so a champion and a challenger both landed in the
                # middle and the baseline probability read as a stalemate.
                "position": ifw.normalise_position(position),
                "segment": ifw.segment_stakeholder(actor.get("entity_type") or "organization", name),
                "influence": actor.get("influence") if isinstance(actor.get("influence"), (int, float)) else 0,
                "rationale": actor.get("relation") or "",
                "position_on_issue": actor.get("relation") or "",
            })

        events = []
        for moment in analysis.get("timeline") or []:
            title = moment.get("event")
            if not title:
                continue
            events.append({
                "title": title,
                "occurred_at": moment.get("date") or None,
                "event_type": "intersection",
                "independent_domains": moment.get("sources"),
            })

        framework_payload = dict(payload)
        framework_payload["issue_outline"] = analysis.get("involvement") or ""
        return ifw.build(
            issue=issue, principal=principal, payload=framework_payload,
            stakeholders=stakeholders, relationships=[], events=events,
            desired_outcome=desired_outcome,
        )
    except Exception as exc:  # the framework is a view; never let it take the map down
        # The Issue Framework tab simply never appears when this fails, which
        # is indistinguishable from a subject the framework had nothing to say
        # about.
        stages.current().failed("issue_map:issue_framework", exc)
        return None

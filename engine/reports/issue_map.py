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

import re
import traceback
from datetime import datetime, timedelta

from engine import stages
from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention
from engine.ingestion.gdelt_connector import GdeltConnector
from engine.reports import (citations, dates, decompose, grounding, issue_floor, issue_graph, prose, protected_terms, relevance, style_check, triangulation)

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_MAX = 250


def _gdelt_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """GDELT DOC full-text search requiring BOTH terms (AND is implicit when
    space-separated quoted phrases are given)."""
    query = f'"{principal}" "{issue}"' if principal else f'"{issue}"'
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
        # The date of publication decides, and nothing may move it — the
        # Search report's rule (`ingestion.base.period_date`), which this
        # path never took: it clamped an older article to the window's first
        # day and stamped an undated one with its last.
        from engine.ingestion.base import period_date

        dated = period_date(GdeltConnector._parse_seendate(art.get("seendate")), ws, we)
        if dated is None:
            continue
        posted, estimated = dated
        out.append(
            IngestedMention(
                platform=art.get("domain") or "news",
                source_type="article",
                author_handle=art.get("domain") or "news",
                text=title,
                posted_at=posted,
                engagement={},
                raw_payload={"url": url, "title": title, "source": "gdelt", "intersection": True,
                             "date_estimated": estimated},
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
    if not identity:
        return issue_term
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


#: The paid endpoints a map asks, best first for "who is connected to what":
#: Kenya-located Google News with exact dates, then the platforms where
#: officials and institutions post, then the rest. Hashtag endpoints need a
#: hashtag, not a phrase, and the brand-mentions composite (50 credits) and
#: comment threads (reactions, not stakeholders) are not asked at all.
MAP_SOCIAL_ENDPOINTS = (
    "/v1/google_news/search", "/v1/twitter/search/tweets", "/v1/facebook/search/posts",
    "/v1/linkedin/search/posts", "/v1/youtube/search", "/v1/tiktok/search",
    "/v1/instagram/search/reels", "/v1/threads/search", "/v1/reddit/search",
)


def _socialcrawl_sweep(identities: list[str], issue_terms: list[str], ws, we, add,
                       report: dict | None) -> None:
    """The paid backbone, one page per endpoint per pair, every page's cost
    counted against `issue_map_credit_budget` BEFORE it is spent. The map
    used to call the connector's whole `fetch` per pair — the 50-credit
    brand-mentions composite, seven searches, and a comment call for every
    post found — with no ceiling at all."""
    import uuid

    from engine.ingestion import socialcrawl_connector as sc

    connector = sc.SocialCrawlConnector()
    endpoints = {path: (platform, kind) for platform, path, kind, _ in sc.SEARCH_ENDPOINTS}
    budget = float(settings.issue_map_credit_budget)
    spent, calls, stopped = 0.0, 0, None
    seen = uuid.uuid4().hex
    for identity, term in _pairs(identities, issue_terms, PAIR_BUDGET["socialcrawl"]):
        query = f"{identity} {term}".strip()
        for path in MAP_SOCIAL_ENDPOINTS:
            if path not in endpoints:
                continue
            cost = sc.credit_cost(path)
            if spent + cost > budget:
                stopped = f"the map's {budget:g}-credit budget was reached"
                break
            if getattr(connector, "credits_exhausted", False):
                stopped = "the SocialCrawl account ran out of credits"
                break
            platform, kind = endpoints[path]
            try:
                rows, _ = connector.search_page(platform, path, query, 1, ws, we, default_source_type=kind,
                                                seen=seen if path in sc.SEEN_PATHS else None)
            except Exception as exc:  # noqa: BLE001 — one refused page costs only itself
                stages.current().failed(f"issue_map:socialcrawl:{platform}", exc)
                continue
            spent += cost
            calls += 1
            add(rows)
        if stopped:
            break
    if report is not None:
        report.update({"credits_spent": spent, "credit_budget": budget, "calls": calls,
                       "stopped": stopped})


# How many identity x issue-name pairs each source is asked for. GDELT and
# Google News are cheap keyless requests and carry the news record, so they get
# the widest sweep; YouTube costs a yt-dlp subprocess per query, so it gets the
# fewest. One literal query per source is what made the issue map shallow —
# these numbers are the fix, and they are here to be tuned rather than buried.
#: Queries on the issue alone, per source — news sources only; social search
#: on a bare issue term is a firehose of reactions.
ISSUE_ALONE_BUDGET = {"gdelt": 1, "google_news": 2}

PAIR_BUDGET = {"gdelt": 8, "google_news": 12, "reddit": 4, "youtube": 3,
               # Paid, and charged per call: fewer pairs, best-first.
               "socialcrawl": 4}


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


def _social_tier() -> tuple[str, float | None, str]:
    """Which social backbone this sweep may use, right now.

    Isolated so a probe failure can never take the map down: unreachable means
    "assume available", the same fail-open the report path uses, because a
    transient blip must not silently downgrade a run.
    """
    try:
        from engine.ingestion.orchestrator import resolve_social_tier

        return resolve_social_tier()
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("issue_map:social_tier", exc)
        return "free", None, f"tier probe failed: {type(exc).__name__}"


def acquire_intersection(
    principal: str,
    issue: str,
    ws: datetime,
    we: datetime,
    identities: list[str] | None = None,
    issue_terms: list[str] | None = None,
    report: dict | None = None,
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
        # The issue on its own as well, for the sources that carry the news
        # record: the analysts need the issue's background, and an issue that
        # is ABOUT the principal ("the government's sale of its Safaricom
        # shares") is often reported without the principal's name in a form
        # a pair query matches. The pools sort what comes back.
        alone = [("", term) for term in issue_terms[:ISSUE_ALONE_BUDGET.get(source, 0)]]
        for identity, term in _pairs(identities, issue_terms, PAIR_BUDGET[source]) + alone:
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

    # The paid tier, decided live. `resolve_social_tier` probes the balance on
    # every call and caches nothing, so topping up credits takes effect on the
    # very next run with no redeploy and no setting to change — and running out
    # mid-run drops back to the free sources instead of burning the run on
    # requests that will 402.
    tier, balance, reason = _social_tier()
    if report is not None:
        report.update({"tier": tier, "balance": balance, "reason": reason})
    if tier == "managed":
        before = len(mentions)
        _socialcrawl_sweep(identities, issue_terms, ws, we, _add, report)
        if report is not None:
            report["mentions"] = len(mentions) - before
    stages.current().ok("issue_map:social_tier", f"{tier} — {reason}")

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
        # Typed from its own name, so "Kenya Government" is not searched as
        # "Hon Government" — person-shaped variants for a person only. An
        # existing subject keeps the type the Search report gave it.
        subject = Politician(name=principal, aliases=principal_variants(principal), keywords=[],
                             subject_type=subject_type_for(principal))
        db.add(subject)
        db.commit()
    return subject


_ORGANISATION_WORDS = re.compile(
    r"\b(government|ministry|court|courts|judiciary|parliament|senate|assembly|county|commission|"
    r"authority|board|agency|council|department|treasury|office|presidency|cabinet|"
    r"bank|ltd|limited|plc|company|corporation|group|holdings|fund|party|university|union|"
    r"society|association|coalition|movement|police|service|kra|sha|imf|iebc|eacc)\b", re.I)


def issue_search_terms(issue: str, aliases: list[str] | None = None) -> list[str]:
    """What the sources are asked for on the issue's side. The typed issue
    went to every source as one quoted phrase; written as a sentence it
    matched nothing anywhere, and a live map on one of the year's largest
    business stories read 16 documents, every kept one from YouTube. Now
    the issue's profile (`reports/issue_profile.py`): its searchable terms,
    what it is also called (SHA is the Social Health Authority), and the
    operator's aliases."""
    from engine.reports import issue_profile

    return issue_profile.profile(issue, aliases)["search_terms"] or [issue]


def map_identities(subject, principal: str) -> list[str]:
    """The principal's search variants for a map: its own Kenyan forms first,
    then whatever the subject row adds — without the person-shaped variants
    ("Hon Government") when the principal is an institution, whatever type
    an older row was stored with."""
    from engine.ingestion import queries

    stored = queries.text_variants(subject)
    if subject_type_for(principal) == "organization":
        stored = [v for v in stored if not re.match(r"^(hon|dr|mr|mrs|ms|prof)\.?\s", v, re.I)]
    seen, out = set(), []
    for v in principal_variants(principal) + stored:
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


class _SearchSubject:
    """The two attributes `queries.intersection_discovery_variants` reads,
    built from the map's own identities rather than the stored row's."""

    def __init__(self, name: str, identities: list[str]):
        self.name = name
        self.aliases = identities[1:]
        self.titles = []
        self.swahili_terms = []
        self.subject_type = "organization"


def subject_type_for(principal: str) -> str:
    """A principal is an organisation when its name says so; otherwise a
    person, the type the rest of the engine assumes."""
    return "organization" if _ORGANISATION_WORDS.search(principal or "") else "politician"


def principal_variants(principal: str) -> list[str]:
    """How the press prints an institution named with the country in it:
    "Kenya Government" is "the Kenyan government" and "the Government of
    Kenya" in every newsroom, and searched only as typed it matched neither."""
    name = re.sub(r"\s+", " ", (principal or "").strip())
    out = [name]
    m = re.match(r"^(Kenya|Kenyan)\s+(.+)$", name, re.I)
    if m:
        rest = m.group(2)
        out += [f"Kenyan {rest}", f"Kenya {rest}", f"{rest} of Kenya"]
    m = re.match(r"^(.+?)\s+of\s+Kenya$", name, re.I)
    if m:
        out += [f"Kenyan {m.group(1)}", f"Kenya {m.group(1)}"]
    seen, unique = set(), []
    for v in out:
        if v.lower() not in seen:
            seen.add(v.lower())
            unique.append(v)
    return unique


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
    # The records as read, in memory only — never stored raw with the map.
    record_items = acquisition.pop("record_items", None) or []

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
        out = _nothing_on_topic(principal, issue, ws, we, mentions, acquisition, report)
        # The press may not connect them where a court record does.
        out["connection_paths"] = _connection_paths(record_items, principal, issue, acquisition)
        from engine.reports import issue_quality

        # The thin map is the one most worth measuring.
        out["quality"] = issue_quality.assess(out, mentions)
        return out

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
    # The connection on the record, and what the public searched, both asked
    # now and read beside the analysts: network and model calls only, no
    # database held across them.
    connection_job = _start_connection_reading(acquisition, principal, mentions)
    digest = build_corpus_digest(label, mentions)
    publish("coverage", digest["coverage"])
    # Four analysts run at once; each publishes the map as it stands the moment
    # its section lands, so the reader gets the actors while the timeline is
    # still being written.
    analysis = analysts.analyze_issue_intersection(
        principal, issue, digest,
        on_part=lambda name, partial: publish("intersection", partial),
    )
    # If every analyst refused, the map is a form with nothing in it after a
    # twenty-minute wait. The documents are right there; count them instead of
    # rendering "not yet established" in every section. Only gaps are filled,
    # and everything filled this way is marked as derived so the page can say
    # where it came from.
    analysis = issue_floor.fill(analysis, mentions, principal, issue)
    # Stakeholders are institutions and individuals; the press that reported
    # them is the evidence, not a party. Set aside and declared.
    from engine.reports import issue_framework as _ifw

    analysis["key_actors"], analysis["media_set_aside"] = _ifw.without_media(analysis.get("key_actors"))
    # The model cites its sources inline as `[ref=fresh-0]` — imitating the
    # digest's own tagging format, because these are free-prose fields with
    # no separate quotes array to carry a citation in. Left alone that string
    # reaches the page verbatim. Resolve it to a real, numbered, clickable
    # citation instead.
    analysis = citations.linkify_analysis(analysis, mentions)
    # Triangulation: how many INDEPENDENT outlets actually back the verdict
    # and each timeline event, not just how many mentions repeat it.
    analysis = triangulation.annotate(analysis)
    # A prompt instruction is not a guarantee. Flag (never silently rewrite)
    # any first-person or hedging language that got through anyway, so a
    # reader can see exactly where house style was violated rather than
    # trusting the instruction worked.
    analysis = style_check.annotate(analysis)
    publish("intersection", analysis)

    # Names the sources do not contain.
    #
    # This is the third time this codebase has run a check on one product
    # and not the other, and the second time the map was the one left out —
    # citations went the other way, triangulation went the other way. The
    # grounding rules are a prompt instruction and this project's own
    # lesson is that an instruction is not a guarantee: quotes are checked
    # against their ref, prose never was. A map is MORE exposed than a
    # report, not less, because its whole claim is a connection between two
    # named things, and an invented third name in that sentence is the
    # fastest way to lose a client.
    #
    # Run on the analysis after linkify so it sees the resolved prose, and
    # reported beside it rather than inside it, so a reader can tell "we
    # looked and found none" from "nobody looked".
    grounding_result = grounding.redact(analysis, grounding.check(analysis, mentions))
    publish("grounding", grounding_result)
    # "Also goes for the issue map as well" — the standing instruction on
    # every change in this product. An invented ethnic attribution is no
    # less defamatory for appearing on the intersection page.
    identity_report = protected_terms.redact(analysis, mentions)
    publish("protected_terms", identity_report)
    prose.map_prose(analysis, dates.britishise)
    if analysis.get("verdict"):
        analysis["verdict"] = our_analysis(analysis["verdict"])

    # Half the sample is reserved for the state's own records, the Search
    # report's rule: a judgment or a Hansard speech has no engagement and
    # was never first in the list, and the record is what a client opens the
    # evidence to check.
    from engine.reports import issue_quality

    records_first = [m for m in mentions if m.get("source_type") in issue_quality.PRIMARY_KINDS][:7]
    rest = [m for m in mentions if m not in records_first]
    sample = [
        {
            "platform": m.get("platform"),
            "source_type": m.get("source_type"),
            "text": (m.get("text") or "")[:400],
            "url": (m.get("raw_payload") or {}).get("url") or m.get("source_url"),
            "posted_at": m.get("posted_at"),
        }
        for m in (records_first + rest)[:15]
    ]

    payload = {
        "principal": principal,
        "issue": issue,
        "window": {"start": ws, "end": we},
        "generated_at": datetime.utcnow(),
        "coverage": digest["coverage"],
        "intersection": analysis,
        "grounding": grounding_result,
        # Kept with the map, not only streamed: what the identity guard
        # removed is part of the record of how the page was made.
        "protected_terms": identity_report,
        "evidence_sample": sample,
        "thin": digest["coverage"]["mentions_total"] == 0,
    }
    if core_count == 0 and mentions:
        # Said once, plainly, rather than left for the reader to infer from
        # sections that quietly have nothing in them.
        payload["intersection_gap"] = {
            "read": len(mentions),
            "note": (f"No source in this window names both “{principal}” and “{issue}” — "
                     f"there is no direct link in the sources' own words. Everything below "
                     f"is each side's own record and the actors around it, and any "
                     f"connection drawn between them is our analysis, not a source's statement."),
        }
    if acquisition:
        # How the corpus was assembled travels with the map. A thin result has
        # to be distinguishable from a collection failure.
        payload["acquisition"] = acquisition

    # The map's first answer, counted from the verified record: every
    # statement that names both the principal and the issue.
    from engine.reports import issue_profile

    the_issue = issue_profile.profile(issue, issue_aliases)
    # What the issue is, kept with the map: its kinds, what it is also
    # called, the bodies that decide it, and which records it is made on.
    payload["issue_profile"] = {k: the_issue[k] for k in
                                ("kinds", "expansions", "institutions", "records", "search_terms")}
    # A statement about the "Social Health Authority" is about SHA: the
    # connection matches on everything the issue is called — and on a name
    # only WITH what is said about it (reports/issue_match.py), so a bare
    # name is never passed here as if it were the issue.
    payload["connection_record"] = _finish_connection(
        connection_job, principal, issue, list(issue_aliases or []), mentions)
    principal_findings = payload["connection_record"].pop("_principal_findings", None)
    publish("connection_record", payload["connection_record"])
    # The connection as a path: the roles the records state — party to a
    # case, speaker in the House, a lender's agency — each step sourced.
    payload["connection_paths"] = _connection_paths(record_items, principal, issue, acquisition)
    publish("connection_paths", payload["connection_paths"])

    # Risk drivers, the probability tree and three scenarios — the model's
    # judgement, held to the record and recomputed (reports/scenarios.py).
    payload["scenarios"] = _scenarios(principal, issue, payload["connection_record"],
                                      principal_findings, desired_outcome)
    if payload["scenarios"].get("state") == "modelled":
        # The identity rule over the scenarios too: a forecast blaming a
        # community no source named is defamatory however it is checked.
        identity = protected_terms.redact(payload["scenarios"], mentions)
        if identity.get("removed"):
            payload["scenarios"]["protected_terms"] = identity
    publish("scenarios", payload["scenarios"])

    # Draw the graph as soon as the intersection lands, before the framework —
    # which is the slowest call in the run. It costs nothing but local work, and
    # it means the reader gets the map minutes earlier instead of watching a
    # stage line. It is rebuilt below once the framework's contours exist.
    _publish_graph(principal, issue, analysis, None, mentions, payload, publish)

    # Dates ahead that a source states — the next hearing, a committee
    # sitting — for the framework's sequencing. No model chooses them.
    try:
        from engine.evidence import upcoming

        # Order papers first: a sitting the record schedules is firmer than a
        # date a news sentence mentions.
        ahead = list((acquisition or {}).get("records_ahead") or [])
        seen = {(a["date"], a["event"][:80]) for a in ahead}
        payload["upcoming"] = ahead + [u for u in upcoming.scan(mentions)
                                       if (u["date"], u["event"][:80]) not in seen]
    except Exception as exc:  # noqa: BLE001 — sequencing falls back to the record
        stages.current().failed("issue_map:upcoming", exc)
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
    # Measured, not believed — Phase 0 of docs/ISSUE_MAP_FOUNDATION.md.
    from engine.reports import issue_quality

    payload["quality"] = issue_quality.assess(payload, mentions)
    publish("quality", payload["quality"])
    return payload


#: Causes that are a family's or an estate's, never the record of a public
#: issue — however often their snippets say "school fees" or "Safaricom".
_PERSONAL_CAUSE = re.compile(
    r"\b(?:Succession|Divorce|Matrimonial|Adoption|Custody|Guardianship)\b|\bEstate of\b"
    r"|\bChildren(?:['’]s)? (?:Case|Cause|Appeal)\b|\((?:Family|Deceased|Child|Minor)s?\)")
#: A party Kenya Law anonymised to initials ("JMA", "MNK & another").
_INITIALS = re.compile(r"[A-Z]{1,4}(?:\s*(?:&|and)\s+(?:another|\d+ others?))?")
#: A party that is a body, not a private person: the state, a company, an
#: association, a public office. A petition by a citizen against a Cabinet
#: Secretary is public; a parent suing a parent over fees is not.
_INSTITUTION = re.compile(
    r"\b(?:Republic|Attorney General|Government|Cabinet Secretary|Principal Secretary|Ministry|County|"
    r"Authority|Commission|Council|Board|Fund|Service|Agency|Corporation|Company|Limited|Ltd|PLC|Bank|"
    r"Union|Association|Society|Trust|Institute|University|College|School|Hospital|Assembly|Parliament|"
    r"Senate|Speaker|Clerk|Registrar|Director|Commissioner|Inspector|Treasury|Party|Coalition|Committee|"
    r"Tribunal|Office|Department|Centre|Center|Foundation|Network|Forum|Movement|Alliance|Federation|"
    r"Chamber|Holdings|Group|Executive|Controller|Auditor|Ombudsman|Directorate|Secretariat|Police|"
    r"National|Kenya|Cooperative|Sacco|Church|Diocese|Mosque|President|Governor|Senator|Minister|"
    r"Chief Justice|Judge|Magistrate|Member of Parliament|Mayor|Chairperson|Chief Executive)\b"
    r"|\bH\.\s?E\.|\bHon\.")


def _is_institution(party: str) -> bool:
    from engine.reports import issue_profile

    if _INSTITUTION.search(party):
        return True
    return any(token in issue_profile.ACRONYMS for token in re.findall(r"\b[A-Z]{2,8}\b", party))


def acquire_records(principal: str, issue: str, issue_aliases: list[str] | None, ws, we,
                    identities: list[str], report: dict, publish=lambda *a: None
                    ) -> tuple[list[IngestedMention], list[dict]]:
    """The primary records an issue is made on (docs/ISSUE_MAP_FOUNDATION.md
    Phase 1), each source in its own thread, each failure the source's own:

      * Kenya Law's case search — every map; the issue's search terms, and
        the principal with the leading term, ten cases a page;
      * Parliament — every map; the Hansard speeches on the issue from the
        sittings in the window, and the order papers ahead that list it;
      * the World Bank — when the issue's kind is money, energy, transport
        or international.

    Returns (corpus items, dated sittings ahead). A source that answered
    nothing says so in `report`, so "no court record" is never produced by
    a refused host."""
    from concurrent.futures import ThreadPoolExecutor

    from engine.ingestion import kenya_law_records, parliament_records, world_bank_records
    from engine.reports import issue_framework, issue_match, issue_profile
    from engine.runctx import carry

    if not settings.enable_issue_map_records:
        report["skipped"] = "switched off (ENABLE_ISSUE_MAP_RECORDS)"
        return [], []
    the_issue = issue_profile.profile(issue, issue_aliases)
    terms = the_issue["search_terms"]
    cutoff = we - timedelta(days=365 * issue_framework.BACKGROUND_YEAR_CUTOFF)
    # The issue's one rule: its names WITH what is said about them. A live
    # run put "let them emulate Safaricom" under the Safaricom share sale
    # and the Rironi-Mau Summit road under Mau forest evictions.
    about = issue_match.of(issue, issue_aliases)

    def set_aside(record: dict) -> str | None:
        """Why a court card is not the record of this issue, or None. A
        search engine ranks on any word; a live run returned an estate case
        for "school fees", "Levi House Construction" for "Housing levy",
        "JMA v SHA" (2019: two people's initials) for SHA, and a child's
        case whose snippet named a party "IMF"."""
        title = record.get("title") or ""
        words = f"{title}. {record.get('snippet') or ''}"
        if _PERSONAL_CAUSE.search(title):
            return "a family, child or estate cause"
        if not about(words):
            return "does not name the issue"
        acronym = about.acronym_only(words)
        if acronym and (any(_INITIALS.fullmatch(p) for p in record.get("parties") or [])
                        or not record.get("parties")):
            return "names the acronym alone, where it may be a party's initials"
        parties = record.get("parties") or []
        if record.get("kind") == "judgment" and parties and not about(title) \
                and not any(_is_institution(p) for p in parties):
            return "a dispute between private parties that mentions the issue"
        return None

    def kenya_law():
        queries = list(dict.fromkeys([*terms[:3], f"{identities[0]} {terms[0]}" if identities else terms[0]]))
        found, notes, aside = [], [], {}
        for query in queries[:4]:
            records, count, why = kenya_law_records.search(query)
            if why:
                notes.append(f"“{query}”: {why}")
            for r in records:
                # Ten years back, the framework's own cut-off: an old case is
                # history, kept with its real date, never moved into the window.
                if r["date"] is not None and r["date"] < cutoff:
                    continue
                reason = set_aside(r)
                if reason:
                    aside[reason] = aside.get(reason, 0) + 1
                else:
                    found.append(r)
        unique = {r["url"]: r for r in found}
        report["kenya_law"] = {"queries": len(queries[:4]), "records": len(unique),
                               "set_aside_off_issue": sum(aside.values()), "set_aside": aside,
                               "notes": notes[:4]}
        return [kenya_law_records.as_mention(r) for r in unique.values()], []

    def parliament():
        sub: dict = {}
        mentions, ahead = parliament_records.fetch(about, ws, we, sub)
        report["parliament"] = sub
        return mentions, ahead

    def world_bank():
        if "world_bank" not in the_issue["records"]:
            report["world_bank"] = {"skipped": "not a money, energy, transport or international issue"}
            return [], []
        out, notes, off = [], [], 0
        for term in terms[:2]:
            for fetch in (world_bank_records.projects, world_bank_records.documents):
                try:
                    for item in fetch(term):
                        # The same two rules as the court record: ten years,
                        # and the item's own words name the issue — a live
                        # run returned a 1964 tea project for "Housing levy".
                        recent = item["raw_payload"].get("date_estimated") or item["posted_at"] >= cutoff
                        if recent and about(item["text"]):
                            out.append(item)
                        else:
                            off += 1
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"{fetch.__name__} “{term}”: {type(exc).__name__}")
        unique = list({(m["raw_payload"].get("url") or m["text"]): m for m in out}.values())
        report["world_bank"] = {"items": len(unique), "set_aside": off, "notes": notes}
        return unique, []

    publish("stage", "Reading the record — court cases, Parliament, the lenders…")
    mentions: list[IngestedMention] = []
    ahead: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        jobs = {name: pool.submit(carry(fn)) for name, fn in
                (("kenya_law", kenya_law), ("parliament", parliament), ("world_bank", world_bank))}
        for name, job in jobs.items():
            try:
                found, later = job.result(timeout=240)
                mentions += found
                ahead += later
            except Exception as exc:  # noqa: BLE001 — one source down costs only itself
                report[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
                stages.current().failed(f"issue_map:records:{name}", exc)
    report["total"] = len(mentions)
    report["kinds"] = the_issue["kinds"]
    return mentions, ahead


def _start_connection_reading(acquisition: dict, principal: str, corpus: list[dict]) -> dict:
    """Plan the fact reading (one query) and start it in a thread. Returns
    the job, or {"reason"} saying why the record cannot be read for this map
    — which the page reports as not measured, never as "no connection"."""
    if not settings.enable_fact_store:
        return {"reason": "the fact store is switched off (ENABLE_FACT_STORE)"}
    subject_id = (acquisition or {}).get("subject_id")
    if not subject_id:
        return {"reason": "this map's sources were not stored, so they could not be read for the record"}
    from concurrent.futures import ThreadPoolExecutor

    from engine.db.session import SessionLocal
    from engine.evidence import facts
    from engine.runctx import carry

    db = SessionLocal()
    try:
        todo = facts.plan(db, corpus, limit=settings.fact_extraction_max_items)
    except Exception as exc:  # noqa: BLE001
        return {"reason": f"planning the reading failed: {type(exc).__name__}: {exc}"[:200]}
    finally:
        db.close()
    future = None
    if todo:
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(carry(lambda: facts.read_all(principal, todo)))
        pool.shutdown(wait=False)
    return {"subject_id": subject_id, "future": future, "to_read": len(todo)}


def _connection_paths(record_items: list, principal: str, issue: str, acquisition: dict | None) -> dict:
    """evidence/paths.py over this map's records. Records that were never
    read (no database, switched off) are said as such — never as "no record
    ties them"."""
    acquisition = acquisition or {}
    if "records" not in acquisition:
        return {"state": "not_measured",
                "reason": "the records were not read for this map" + (
                    f" ({acquisition['reason']})" if acquisition.get("reason") else "")}
    try:
        from engine.evidence import paths

        identities = acquisition.get("identities") or principal_variants(principal)
        return paths.build(record_items, principal, identities, issue,
                           ahead=acquisition.get("records_ahead"), sources=acquisition.get("records"))
    except Exception as exc:  # noqa: BLE001 — the path is one section; the map stands
        stages.current().failed("issue_map:connection_paths", exc)
        return {"state": "not_measured", "reason": f"{type(exc).__name__}: {exc}"[:200]}


def _finish_connection(job: dict, principal: str, issue: str, aliases: list[str] | None,
                       corpus: list[dict]) -> dict:
    from concurrent.futures import TimeoutError as FuturesTimeout

    from engine.db.models import Politician
    from engine.db.session import SessionLocal
    from engine.evidence import connection, facts

    if "reason" in job:
        return connection.not_measured(job["reason"])
    db = SessionLocal()
    note = None
    try:
        subject = db.get(Politician, job["subject_id"])
        if job.get("future") is not None:
            try:
                results = job["future"].result(timeout=settings.fact_store_wait_seconds)
                facts.store(db, subject, results)
            except FuturesTimeout:
                note = ("some sources were still being read when the map finished; "
                        "the next map on this pair reads them")
        record = facts.record_for(db, subject, corpus, most=50, keep_all=True)
        co_mentioned = sum(1 for m in corpus or [] if m.get("evidence_pool") == "core")
        out = connection.build(record.get("all_findings") or [], principal, issue, aliases,
                               co_mentioned=co_mentioned)
        out["sources_read_this_run"] = job.get("to_read", 0)
        # The principal's whole record, for the scenario writer's context;
        # popped before the payload is published or stored.
        out["_principal_findings"] = record.get("all_findings") or []
        if note:
            out["note"] = note
        return out
    except Exception as exc:  # noqa: BLE001 — never the map's cost
        traceback.print_exc()
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return connection.not_measured(f"{type(exc).__name__}: {exc}"[:200])
    finally:
        db.close()


_OPENERS = re.compile(r"^(?:the record|the sources|the evidence)\s+(?:shows?|indicates?|suggests?)\s+(?:that\s+)?", re.I)


def our_analysis(verdict: str) -> str:
    """The verdict is the firm's reading, so it opens "Our analysis
    indicates…" (the founder's note on a live map). Asked for in the prompt;
    held here, because a prompt instruction is not a guarantee. "The record
    connects X…" read as though the record had said it, while the connection
    section beneath said the record did not."""
    text = (verdict or "").strip()
    if not text or text.lower().startswith("our analysis"):
        return text
    rest = _OPENERS.sub("", text)
    first = rest.split(" ", 1)[0]
    if first in ("The", "This", "These", "That", "A", "An", "It", "There"):
        rest = rest[0].lower() + rest[1:]
    return "Our analysis indicates that " + rest


def _scenarios(principal: str, issue: str, connection_record: dict | None,
               principal_findings: list[dict] | None, desired_outcome: str | None) -> dict:
    """The scenario section, or the reason it was not modelled — a failure
    here is the operator's to read and never costs the map."""
    from engine.reports import scenarios

    counts = (connection_record or {}).get("counts") or {}
    numbers = {str(v) for v in counts.values() if isinstance(v, int)}
    try:
        out = scenarios.build(principal, issue, connection_record, principal_findings,
                              numbers=numbers, desired_outcome=desired_outcome)
    except Exception as exc:  # noqa: BLE001
        stages.current().failed("issue_map:scenarios", exc)
        return scenarios.withheld(f"the scenario writer failed: {type(exc).__name__}: {exc}"[:200])
    if out.get("state") == "modelled":
        stages.current().ok("issue_map:scenarios",
                            f"{len(out['drivers'])} drivers, {len(out['scenarios'])} scenarios")
    else:
        stages.current().skipped("issue_map:scenarios", out.get("reason") or "not modelled")
    return out


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


def _reconcile_relevance_report(relevance_report: dict, corpus: list[dict]) -> dict:
    """partition_corpus's "kept" counts every relevant document found, before
    _blend_pools caps each pool to POOL_BUDGET. Once blended, "kept" has to
    mean what the analyst actually reads — otherwise Data Overview reports
    one "documents on topic" number while the digest and coverage chips
    report the smaller, budget-capped count, and the two disagree in the
    same report."""
    relevance_report["kept"] = len(corpus)
    relevance_report["dropped"] = relevance_report["examined"] - len(corpus)
    return relevance_report


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
        identities = map_identities(subject, principal)
        issue_terms = issue_search_terms(issue, issue_aliases)
        # The discovery layers take the issue's searchable terms, never the
        # typed sentence, and the principal's own variants.
        discovery = queries.intersection_discovery_variants(
            _SearchSubject(principal, identities), issue_terms[0], issue_terms[1:])
    except Exception:  # noqa: BLE001 — no database is not a reason to fail
        traceback.print_exc()
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        publish("stage", f"Searching for “{principal}” × “{issue}”…")
        fresh = acquire_intersection(principal, issue, ws, we,
                                     identities=principal_variants(principal),
                                     issue_terms=issue_search_terms(issue, issue_aliases))
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
        from engine.reports import issue_profile

        principal_parts = decompose.decompose(principal)
        match_identities = relevance.merge_terms(identities, principal_parts["identities"])
        match_issue_terms = relevance.merge_terms(issue_terms,
                                                  issue_profile.profile(issue, issue_aliases)["identities"])

        dimensions = decompose.research_dimensions(
            principal, issue, window_start=ws,
            institutions=issue_profile.profile(issue, issue_aliases)["institutions"])
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
        social_report: dict = {}
        fresh = acquire_intersection(principal, issue, ws, we,
                                     identities=identities, issue_terms=issue_terms,
                                     report=social_report)
        if social_report.get("tier") == "managed":
            publish("stage", f"Paid social backbone active — "
                             f"{social_report.get('mentions', 0)} platform mentions added…")
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

        # The records a connection is made on — court cases, Parliament,
        # the lenders — stored with the rest so they compound across maps.
        records_report: dict = {}
        record_mentions, records_ahead = acquire_records(
            principal, issue, issue_aliases, ws, we, identities, records_report, publish)
        fresh = list(fresh) + record_mentions
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
        # The issue's one rule (reports/issue_match.py): its names WITH
        # what is said about them. "Safaricom" alone is not the share sale.
        from engine.reports import issue_match

        the_match = issue_match.of(issue, issue_aliases)
        pools, relevance_report = relevance.partition_corpus(
            corpus, match_identities, match_issue_terms, require_market, issue_test=the_match)
        relevance_report["matched_on"] = {
            "principal": match_identities[:6], "issue": the_match.describe()}
        corpus = _blend_pools(pools)
        relevance_report = _reconcile_relevance_report(relevance_report, corpus)
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
            "social_tier": social_report,
            "records": records_report,
            "records_ahead": records_ahead,
            # In memory only: the payload builder reads the records' roles
            # for the evidence path and drops these before anything is
            # stored (`_connection_paths`).
            "record_items": record_mentions,
            "identities": identities,
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
        social_report: dict = {}
        fresh = acquire_intersection(principal, issue, ws, we,
                                     identities=identities, issue_terms=issue_terms,
                                     report=social_report)
        if social_report.get("tier") == "managed":
            publish("stage", f"Paid social backbone active — "
                             f"{social_report.get('mentions', 0)} platform mentions added…")
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
                # Undated items carry the flag through, as they do in the
                # Search report: "we could not date this" must never read as
                # a date in the map's timeline.
                "date_estimated": bool(raw.get("date_estimated")),
                # Read in full — the article enricher keeps the body here.
                "full_text": bool(raw.get("article_text")),
                # Which half of the question it answers — "core" names both.
                # Dropped here, the connection could not tell "named together,
                # never linked" from "never named together".
                "evidence_pool": m.get("evidence_pool"),
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


def _actor_relationships(analysis: dict, known: set) -> list[dict]:
    """Who is tied to whom, from the record rather than from nothing.

    `relationships=[]` was passed in literally, so the stakeholder network has
    rendered without a single edge on every issue map ever produced — a
    "network" section that was a list with a picture of dots beside it.

    Three sources, all already in the analysis and all evidenced: two actors
    named in the same timeline moment, two actors on the same sub-issue, and
    two actors holding the same stance on the issue itself. Directed both ways
    so either name finds the other.
    """
    edges: dict[tuple, dict] = {}

    def link(a: str, b: str, rel_type: str) -> None:
        if a == b or a not in known or b not in known:
            return
        for source, target in ((a, b), (b, a)):
            entry = edges.get((source, target))
            if entry is None:
                edges[(source, target)] = {"source": source, "target": target,
                                           "rel_type": rel_type, "weight": 1}
            else:
                entry["weight"] += 1

    def named_in(text: str) -> list[str]:
        haystack = str(text or "").lower()
        return [name for name in known if name and name.lower() in haystack]

    for moment in analysis.get("timeline") or []:
        if isinstance(moment, dict):
            present = named_in(moment.get("event"))
            for i, left in enumerate(present):
                for right in present[i + 1:]:
                    link(left, right, "named in the same development")

    for sub in analysis.get("sub_issues") or []:
        if not isinstance(sub, dict):
            continue
        on_it = [n for n in (sub.get("actors") or []) if n in known]
        for i, left in enumerate(on_it):
            for right in on_it[i + 1:]:
                link(left, right, f"both on: {sub.get('sub_issue')}")

    by_stance: dict[str, list[str]] = {}
    for actor in analysis.get("key_actors") or []:
        if isinstance(actor, dict) and actor.get("name") in known:
            by_stance.setdefault(str(actor.get("position") or "neutral").lower(), []) \
                .append(actor["name"])
    for stance, names in by_stance.items():
        if stance in ("", "neutral") or len(names) < 2:
            continue
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                link(left, right, f"same stance ({stance})")

    return sorted(edges.values(), key=lambda e: -e["weight"])


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
                # The framework's hover profile. These three keys were read by
                # build_stakeholder_networks and never written by anything, so
                # every profile rendered as three empty fields under a name.
                "history": actor.get("history") or actor.get("track_record") or "",
                "track_record": actor.get("track_record") or "",
                "modus_operandi": actor.get("modus_operandi") or "",
            })

        events = []
        for moment in analysis.get("timeline") or []:
            title = moment.get("event")
            if not title:
                continue
            # "sources" is never written by the analyst prompt — reading it
            # here always landed on None, so this event was counted as
            # single-source in Data Overview regardless of what the SAME
            # moment's `corroboration` (built by triangulation.annotate,
            # from this exact timeline's citations) says a few sections
            # later in the same report.
            corroboration = moment.get("corroboration") or {}
            events.append({
                "title": title,
                "occurred_at": moment.get("date") or None,
                "event_type": "intersection",
                "independent_domains": corroboration.get("independent_sources"),
                # Carry the resolved citations with the prose, or the page
                # renders "[1]" with nothing behind it.
                "event_citations": moment.get("event_citations") or [],
            })

        relationships = _actor_relationships(analysis, {s["name"] for s in stakeholders})

        framework_payload = dict(payload)
        framework_payload["issue_outline"] = analysis.get("involvement") or ""
        # Section 1's two halves. `build_background` has always read these keys
        # and nothing has ever written them, so "international" and "national"
        # rendered empty on every map regardless of how much was collected.
        framework_payload["international_context"] = analysis.get("international") or ""
        framework_payload["national_context"] = analysis.get("national") or ""
        framework_payload["international_citations"] = analysis.get("international_citations") or []
        framework_payload["national_citations"] = analysis.get("national_citations") or []
        framework = ifw.build(
            issue=issue, principal=principal, payload=framework_payload,
            stakeholders=stakeholders, relationships=relationships, events=events,
            desired_outcome=desired_outcome,
        )

        # "— not yet established from the evidence" blames the corpus. Usually
        # the corpus is fine and the analyst that writes this section never
        # answered, and those are opposite findings: one says the record is
        # silent, the other says we did not read it. Say which.
        background = framework.get("background_and_context") or {}
        if not (background.get("international") or background.get("national")):
            wrote_nothing = not analysis.get("key_actors") or bool(
                analysis.get("derived_sections"))
            background["unavailable"] = (
                "The analyst that writes this section did not answer, so it was not "
                "written. This is not a finding about the record."
                if wrote_nothing else
                "No document collected described the issue in its own right, "
                "independent of the principal.")
        return framework
    except Exception as exc:  # the framework is a view; never let it take the map down
        # The Issue Framework tab simply never appears when this fails, which
        # is indistinguishable from a subject the framework had nothing to say
        # about.
        stages.current().failed("issue_map:issue_framework", exc)
        return None

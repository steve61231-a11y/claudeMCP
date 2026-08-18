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

from datetime import datetime, timedelta

from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention
from engine.ingestion.gdelt_connector import GdeltConnector

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
    except Exception:
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


def _google_news_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """Google News RSS requiring BOTH terms — the strongest free source for the
    Kenyan-politics intersection (local outlets GDELT misses). Reuses the
    connector but with a fixed both-terms query via aliases."""
    from engine.ingestion.google_news_rss_connector import GoogleNewsRssConnector

    conn = GoogleNewsRssConnector()
    # The connector ORs name+aliases; to require BOTH terms we pass a single
    # combined phrase as the "name" and no aliases.
    return conn.fetch(f'{principal}" "{issue}', [], ws, we)


def _reddit_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    from engine.ingestion.reddit_connector import RedditConnector

    return RedditConnector().fetch(f"{principal} {issue}", [], ws, we)


def acquire_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """Gather mentions that connect the principal and the issue. Best-effort
    across ALL free sources (GDELT + Google News RSS + Reddit); enriches article
    bodies so the digest reads full journalism at the intersection."""
    mentions: list[IngestedMention] = []
    seen: set[str] = set()

    def _add(items):
        for m in items or []:
            url = (m.get("raw_payload") or {}).get("url") or m.get("text", "")[:80]
            if url and url not in seen:
                seen.add(url)
                mentions.append(m)

    if settings.enable_gdelt:
        _add(_gdelt_intersection(principal, issue, ws, we))
    if settings.enable_google_news:
        _add(_google_news_intersection(principal, issue, ws, we))
    if settings.enable_reddit:
        _add(_reddit_intersection(principal, issue, ws, we))

    if mentions:
        from engine.ingestion.article_text import enrich_with_article_text

        enrich_with_article_text(mentions)
    return mentions


def build_issue_map(
    principal: str,
    issue: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    mentions: list[dict] | None = None,
    desired_outcome: str | None = None,
) -> dict:
    """Produce the issue-map payload for principal × issue.

    Acquires (or accepts injected) intersection mentions, runs the whole-corpus
    map-reduce digest for provable full coverage, then the issue-intersection
    analyst. Returns a self-describing payload including a coverage record and a
    small evidence sample.
    """
    from engine.reports import analysts
    from engine.reports.digest import build_corpus_digest

    we = window_end or datetime.utcnow()
    ws = window_start or (we - timedelta(days=365))

    if mentions is None:
        mentions = acquire_intersection(principal, issue, ws, we)

    label = f"{principal} × {issue}"
    digest = build_corpus_digest(label, mentions)
    analysis = analysts.analyze_issue_intersection(principal, issue, digest)

    sample = [
        {
            "platform": m.get("platform"),
            "text": (m.get("text") or "")[:400],
            "url": (m.get("raw_payload") or {}).get("url"),
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
    payload["issue_framework"] = _issue_framework(principal, issue, payload, analysis,
                                                  desired_outcome=desired_outcome)
    return payload


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
                "position": position if position in ("for", "against", "neutral") else "neutral",
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
    except Exception:  # the framework is a view; never let it take the map down
        return None

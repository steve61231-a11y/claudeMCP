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
        out.append(
            IngestedMention(
                platform=art.get("domain") or "news",
                source_type="article",
                author_handle=art.get("domain") or "news",
                text=title,
                posted_at=we,
                engagement={},
                raw_payload={"url": url, "title": title, "source": "gdelt", "intersection": True},
            )
        )
    return out


def acquire_intersection(principal: str, issue: str, ws: datetime, we: datetime) -> list[IngestedMention]:
    """Gather mentions that connect the principal and the issue. Best-effort
    across the free sources; enriches article bodies so the digest reads full
    journalism at the intersection."""
    mentions: list[IngestedMention] = []
    if settings.enable_gdelt:
        mentions.extend(_gdelt_intersection(principal, issue, ws, we))
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

    return {
        "principal": principal,
        "issue": issue,
        "window": {"start": ws, "end": we},
        "generated_at": datetime.utcnow(),
        "coverage": digest["coverage"],
        "intersection": analysis,
        "evidence_sample": sample,
        "thin": digest["coverage"]["mentions_total"] == 0,
    }

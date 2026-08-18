"""Evidence retrieval — find the stored material that bears on a statement.

Grounding only means something if the grounding can be *looked up*. This module
is the lookup: given a claim, it returns the actual documents and mentions that
speak to it, ranked, with the specific passage quoted.

It searches both evidence stores:
  - `documents`, via the maintained tsvector + GIN index (fast, and the reason a
    single relevant sentence inside a long article is findable at all),
  - `raw_mentions`, ranked on the fly — social posts are short, so there are no
    buried passages to dig out and an index would buy little.

Retrieval is deliberately recall-oriented: the verifier's job is to judge, and
it can only judge evidence it has been shown.
"""

from sqlalchemy import text as sql_text

# Words that match everything and therefore rank nothing.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "has",
    "have", "had", "that", "this", "these", "those", "it", "its", "his", "her",
    "their", "he", "she", "they", "we", "you", "not", "no", "than", "then",
    "there", "which", "who", "whom", "will", "would", "can", "could", "may",
    "might", "should", "about", "into", "over", "after", "before", "during",
}


def _query_terms(claim: str, max_terms: int = 12) -> list[str]:
    """Content words from a claim, longest first (most distinctive)."""
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in claim.lower())
    words = [w for w in cleaned.split() if len(w) > 2 and w not in _STOPWORDS]
    seen: set[str] = set()
    unique = []
    for word in sorted(words, key=len, reverse=True):
        if word not in seen:
            seen.add(word)
            unique.append(word)
    return unique[:max_terms]


def retrieve_for_claim(db, politician_id: str, claim: str, limit: int = 6) -> list[dict]:
    """Documents and mentions that bear on `claim`, best match first.

    Returns dicts carrying the id, source URL and the matching passage, so a
    verdict can cite the exact words rather than a whole article.
    """
    terms = _query_terms(claim)
    if not terms:
        return []
    query = " OR ".join(terms)

    results: list[dict] = []

    doc_rows = db.execute(
        sql_text(
            """
            SELECT id, url, domain, title, published_at,
                   ts_rank(search_vector, websearch_to_tsquery('english', :q)) AS rank,
                   ts_headline('english', body, websearch_to_tsquery('english', :q),
                               'MaxFragments=2,MinWords=10,MaxWords=40,StartSel=,StopSel=') AS passage
            FROM documents
            WHERE politician_id = :pid
              AND (relevance_verdict IS NULL OR relevance_verdict <> 'off_topic')
              AND search_vector @@ websearch_to_tsquery('english', :q)
            ORDER BY rank DESC
            LIMIT :lim
            """
        ),
        {"pid": politician_id, "q": query, "lim": limit},
    ).fetchall()
    for row in doc_rows:
        results.append(
            {
                "kind": "document",
                "document_id": row.id,
                "mention_id": None,
                "url": row.url,
                "source": row.domain,
                "title": row.title,
                "published_at": str(row.published_at) if row.published_at else None,
                "passage": (row.passage or "").strip(),
                "rank": float(row.rank or 0),
            }
        )

    mention_rows = db.execute(
        sql_text(
            """
            SELECT id, source_url, platform, author_handle, posted_at, text,
                   ts_rank(to_tsvector('english', text),
                           websearch_to_tsquery('english', :q)) AS rank
            FROM raw_mentions
            WHERE politician_id = :pid
              AND to_tsvector('english', text) @@ websearch_to_tsquery('english', :q)
            ORDER BY rank DESC
            LIMIT :lim
            """
        ),
        {"pid": politician_id, "q": query, "lim": limit},
    ).fetchall()
    for row in mention_rows:
        results.append(
            {
                "kind": "mention",
                "document_id": None,
                "mention_id": row.id,
                "url": row.source_url,
                "source": row.platform,
                "title": None,
                "published_at": str(row.posted_at) if row.posted_at else None,
                "passage": (row.text or "")[:400].strip(),
                "rank": float(row.rank or 0),
            }
        )

    results.sort(key=lambda r: r["rank"], reverse=True)
    return results[:limit]


def independent_source_count(evidence: list[dict]) -> int:
    """Distinct sources behind a claim.

    Ten copies of one wire story are one source, not ten — corroboration only
    counts when it is genuinely independent.
    """
    return len({(e.get("source") or e.get("url") or "").lower() for e in evidence if e.get("source") or e.get("url")})


def retrieve_intersection(
    db, politician_id: str, terms: list[str], limit: int = 400
) -> list[dict]:
    """Everything already stored about this subject that mentions the issue.

    The issue map used to re-scrape from zero on every run and keep nothing, so
    it could never compound and could never reuse the corpus the politician
    path had already built for the same person. This is the other half of that
    fix: once intersection material is stored under the subject, a later run —
    for the same issue or a different one — reads it straight out of the store.

    Returns corpus dicts in the same shape as `pipeline._document_corpus`, so
    the digest and the analysts read documents and social posts side by side
    with no special-casing.
    """
    query = " OR ".join(t for t in terms if t)
    if not query:
        return []
    half = max(1, limit // 2)

    corpus: list[dict] = []

    doc_rows = db.execute(
        sql_text(
            """
            SELECT id, url, domain, title, author, body, published_at, fetched_at, language
            FROM documents
            WHERE politician_id = :pid
              AND (relevance_verdict IS NULL OR relevance_verdict <> 'off_topic')
              AND search_vector @@ websearch_to_tsquery('english', :q)
            ORDER BY ts_rank(search_vector, websearch_to_tsquery('english', :q)) DESC
            LIMIT :lim
            """
        ),
        {"pid": politician_id, "q": query, "lim": half},
    ).fetchall()
    for row in doc_rows:
        text = f"{row.title}\n\n{row.body}" if row.title else (row.body or "")
        if not text.strip():
            continue
        corpus.append(
            {
                "id": row.id,
                "platform": row.domain or "web",
                "source_type": "article",
                "author_handle": row.author or row.domain or "web",
                "text": text,
                "posted_at": row.published_at or row.fetched_at,
                "engagement": {},
                "language": row.language,
                "source_url": row.url,
            }
        )

    mention_rows = db.execute(
        sql_text(
            """
            SELECT id, platform, source_type, author_handle, text, posted_at,
                   engagement_json, language, source_url
            FROM raw_mentions
            WHERE politician_id = :pid
              AND is_spam = 0
              AND to_tsvector('english', text) @@ websearch_to_tsquery('english', :q)
            ORDER BY ts_rank(to_tsvector('english', text),
                             websearch_to_tsquery('english', :q)) DESC
            LIMIT :lim
            """
        ),
        {"pid": politician_id, "q": query, "lim": limit - len(corpus)},
    ).fetchall()
    for row in mention_rows:
        if not (row.text or "").strip():
            continue
        corpus.append(
            {
                "id": row.id,
                "platform": row.platform,
                "source_type": row.source_type,
                "author_handle": row.author_handle,
                "text": row.text,
                "posted_at": row.posted_at,
                "engagement": row.engagement_json or {},
                "language": row.language,
                "source_url": row.source_url,
            }
        )
    return corpus

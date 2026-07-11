"""Full-article extraction — turn headline-only article mentions into the full
readable body of the journalism.

GDELT, NewsAPI and Wayback return a title + snippet, not the article body, so
the deepest source we have (long-form reporting) is under-read. This module
fetches each article URL and extracts the main text with `trafilatura`
(pure-python, keyless, no browser) — the same library newsrooms use for
boilerplate-free body extraction.

Enrichment is:
  - opt-in via `settings.enable_article_text` (default on),
  - bounded (`article_text_max_fetch` URLs per run, `article_text_max_chars`
    body per article) so a run can't blow up latency or memory,
  - best-effort and non-fatal: a fetch/parse failure leaves the mention's
    original title text untouched, never raising.

The extracted body is appended to the mention's `text` (so downstream digest,
sentiment and grounding all read it) and stashed in
`raw_payload["article_text"]` for provenance.
"""

from concurrent.futures import ThreadPoolExecutor

from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention

# Only these source types are article-shaped and worth a full-body fetch.
_ARTICLE_SOURCE_TYPES = {"article", "news"}
_FETCH_WORKERS = 6
_FETCH_TIMEOUT = 20


def _mention_url(mention: IngestedMention) -> str | None:
    raw = mention.get("raw_payload") or {}
    for key in ("url", "link", "article_url", "permalink"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None


def extract_body(url: str, max_chars: int) -> str:
    """Fetch `url` and return the boilerplate-free article body, or "" on any
    failure. Never raises."""
    try:
        import trafilatura
    except Exception:
        return ""
    try:
        resp = http.get(url, timeout=_FETCH_TIMEOUT)
        if resp.status_code != 200 or not resp.text:
            return ""
        body = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
    except Exception:
        return ""
    if not body:
        return ""
    return body.strip()[:max_chars]


def enrich_with_article_text(mentions: list[IngestedMention]) -> int:
    """In-place: fetch full body text for article-shaped mentions that carry a
    URL, appending it to `text` and recording it in `raw_payload`. Returns the
    number of mentions successfully enriched.

    Bounded by `settings.article_text_max_fetch`; a no-op (returns 0) when the
    feature is disabled or trafilatura is unavailable."""
    if not settings.enable_article_text or not mentions:
        return 0

    candidates: list[tuple[IngestedMention, str]] = []
    for m in mentions:
        if m.get("source_type") not in _ARTICLE_SOURCE_TYPES:
            continue
        raw = m.get("raw_payload") or {}
        if raw.get("article_text"):  # already enriched (e.g. Wayback recover)
            continue
        url = _mention_url(m)
        if url:
            candidates.append((m, url))
        if len(candidates) >= settings.article_text_max_fetch:
            break

    if not candidates:
        return 0

    max_chars = settings.article_text_max_chars

    def _work(pair):
        m, url = pair
        return m, extract_body(url, max_chars)

    enriched = 0
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(candidates))) as pool:
        for m, body in pool.map(_work, candidates):
            if not body:
                continue
            raw = m.get("raw_payload") or {}
            raw["article_text"] = body
            m["raw_payload"] = raw
            title = (m.get("text") or "").strip()
            # Don't duplicate the title if the body already leads with it.
            m["text"] = body if title and body.startswith(title) else f"{title}\n\n{body}".strip()
            enriched += 1
    return enriched

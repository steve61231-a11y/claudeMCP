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
from engine import stages
from engine.ingestion import fetch_backend
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


def extract_body(url: str, max_chars: int) -> tuple[str, str | None]:
    """Fetch `url` and return `(body, backend)` — the boilerplate-free article
    text and which fetch tier produced it. `("", backend)` on any failure.

    The fetch goes through `fetch_backend`, which retries a bot-walled request
    with a browser TLS fingerprint before giving up; a 403 from Cloudflare is
    the single commonest reason a Kenyan article contributes only its headline.
    Never raises."""
    try:
        import trafilatura
    except Exception as exc:  # noqa: BLE001
        # Without trafilatura EVERY article contributes only its headline, and
        # the report simply looks like a corpus of thin items.
        stages.current().failed("article_text:trafilatura_missing", exc)
        return "", None
    result = fetch_backend.fetch_html(url, timeout=_FETCH_TIMEOUT)
    if not result.ok:
        return "", result.backend
    try:
        body = trafilatura.extract(
            result.html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
    except Exception:
        return "", result.backend
    if not body:
        return "", result.backend
    return body.strip()[:max_chars], result.backend


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
        body, backend = extract_body(url, max_chars)
        return m, body, backend

    enriched = 0
    attempted = len(candidates)
    with ThreadPoolExecutor(max_workers=min(_FETCH_WORKERS, len(candidates))) as pool:
        for m, body, backend in pool.map(_work, candidates):
            if not body:
                continue
            raw = m.get("raw_payload") or {}
            raw["article_text"] = body
            # Provenance: which tier got past the site's door.
            raw["article_text_backend"] = backend
            m["raw_payload"] = raw
            title = (m.get("text") or "").strip()
            # Don't duplicate the title if the body already leads with it.
            m["text"] = body if title and body.startswith(title) else f"{title}\n\n{body}".strip()
            enriched += 1
    # How much of the journalism actually made it in. Nobody read this return
    # value, so a run where every body fetch was refused looked exactly like a
    # run of headline-only sources.
    if enriched < attempted:
        stages.current().record(
            "article_text", stages.STATUS_OK if enriched else stages.STATUS_FAILED,
            detail=f"{enriched} of {attempted} article bodies extracted; the rest "
                   f"contribute only their headline "
                   f"(fetch tiers: {fetch_backend.snapshot()})")
    return enriched

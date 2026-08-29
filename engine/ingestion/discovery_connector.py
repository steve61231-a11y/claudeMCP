"""Web discovery via a self-hosted SearXNG metasearch instance.

Fixed connectors answer "what is being said now" on the platforms we wired up.
Discovery answers the harder question — "what exists about this subject
anywhere, across every kind of source and every period" — because the detail
that reshapes a conclusion can sit anywhere, and comprehensiveness is the whole
point of due diligence.

SearXNG fans a single query across 200+ engines (Google, Bing, Brave, DDG,
news verticals, archives) and returns JSON. We expand each subject into queries
covering every dimension of its public record (see
`queries.discovery_variants`), collect the union of results, then fetch each
URL's full body text — never just the snippet — so nothing is summarised away
before analysis and any passage remains retrievable.

Contract, same as every other connector: bounded, best-effort, never raises.
No SearXNG instance configured (or unreachable) simply means no discovery.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlparse

from engine.config import settings
from engine.ingestion import http

_FETCH_WORKERS = 6
# Metasearch calls are IO-bound and the instance is ours, but stay polite so
# SearXNG's own upstream engines don't rate-limit it.
_SEARCH_WORKERS = 5

# Aggregators, social shells and search-engine chrome carry no evidentiary text.
_SKIP_DOMAINS = {
    "google.com", "bing.com", "duckduckgo.com", "search.marcia.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "pinterest.com",
    "youtube.com",  # handled by the dedicated YouTube connector
}


def _normalise_base_url(raw: str | None) -> str:
    """A usable base URL, with a scheme.

    Render's internal hostnames are given as `service:port`, which is exactly
    what its dashboard shows and exactly what an operator pastes in. Without a
    scheme `requests` raises InvalidSchema, the blanket except below turns that
    into an empty result list, and the whole discovery layer — the eighty-probe
    full-text sweep, the largest single source of depth in a report — is dead
    with no error anywhere. That is a config typo costing the product its best
    feature, silently, so it is corrected here rather than diagnosed later.
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    return value


class DiscoveryConnector:
    """Queries SearXNG and returns full-text documents (not mentions).

    Discovery yields long-form evidence, so results are stored as `Document`
    rows rather than `RawMention` — see orchestrator._store_documents.
    """

    def __init__(self, base_url: str | None = None, searcher=None, fetcher=None):
        self.base_url = _normalise_base_url(
            base_url if base_url is not None else settings.searxng_url
        )
        # Why the last search returned nothing, when it returned nothing.
        self.last_error: str | None = None
        # Both seams are injectable so tests never touch the network.
        self._searcher = searcher
        self._fetcher = fetcher

    # --- seams -------------------------------------------------------------

    def _search(self, query: str) -> list[dict]:
        """One metasearch call. Returns raw SearXNG result dicts, or []."""
        if self._searcher is not None:
            return self._searcher(query)
        if not self.base_url:
            return []
        try:
            response = http.get(
                f"{self.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": "en",
                    "safesearch": "0",
                },
                timeout=settings.discovery_timeout,
            )
            if response.status_code != 200:
                self.last_error = f"HTTP {response.status_code} from {self.base_url}/search"
                return []
            self.last_error = None
            return response.json().get("results", []) or []
        except Exception as exc:  # noqa: BLE001 — discovery must never break a run
            # Recorded, not just swallowed. A misconfigured URL and a genuinely
            # empty result set are indistinguishable from the caller otherwise,
            # and that is how a dead discovery layer went unnoticed: the
            # diagnostic said "0 results — check the instance is up" while the
            # real fault was a URL with no scheme.
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return []

    def _fetch_body(self, url: str) -> str:
        if self._fetcher is not None:
            return self._fetcher(url)
        from engine.ingestion.article_text import extract_body

        body, _backend = extract_body(url, settings.article_text_max_chars)
        return body

    # --- public API --------------------------------------------------------

    def discover(self, queries: list[str]) -> list[dict]:
        """Run every query, dedupe by URL, and return candidate documents.

        Ordering is preserved so the most-specific identity queries win the
        dedupe race and keep their (better) title/snippet.
        """
        seen: set[str] = set()
        candidates: list[dict] = []
        cap = settings.discovery_max_results_per_query

        # Dozens of probe queries per subject: run them concurrently or discovery
        # alone would dominate a run. Results are zipped back in submission order
        # so the dedupe below stays deterministic.
        with ThreadPoolExecutor(max_workers=_SEARCH_WORKERS) as pool:
            per_query = list(pool.map(self._search, queries))

        for query, results in zip(queries, per_query):
            for result in results[:cap]:
                url = (result.get("url") or "").strip()
                if not url.startswith("http"):
                    continue
                key = _canonical_url(url)
                if key in seen:
                    continue
                domain = _domain(url)
                if not domain or domain in _SKIP_DOMAINS:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "url": url,
                        "domain": domain,
                        "title": (result.get("title") or "").strip(),
                        "snippet": (result.get("content") or "").strip(),
                        "published_at": _parse_date(result.get("publishedDate")),
                        "engine": result.get("engine") or "searxng",
                        "found_by_query": query,
                    }
                )
        return candidates

    def fetch_documents(self, subject_name: str, aliases: list[str], queries: list[str]) -> list[dict]:
        """Discover URLs, pull full text, and keep only on-topic documents.

        The relevance check here is deliberately cheap and deterministic: does
        the fetched text actually name the subject? Metasearch is broad by
        design, and without this a homonym ("SHA" in another language/domain)
        would flood the corpus. The LLM disambiguation gate refines this
        further downstream.
        """
        candidates = self.discover(queries)[: settings.discovery_max_fetch]
        if not candidates:
            return []

        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            bodies = list(pool.map(lambda c: self._fetch_body(c["url"]), candidates))

        terms = [t.lower() for t in [subject_name, *aliases] if t]
        surname = subject_name.strip().split()[-1].lower() if subject_name.strip() else ""
        if surname and len(surname) > 3:
            terms.append(surname)

        documents: list[dict] = []
        for candidate, body in zip(candidates, bodies):
            text = body or candidate["snippet"]
            if not text:
                continue
            haystack = f"{candidate['title']} {text}".lower()
            if terms and not any(term in haystack for term in terms):
                continue  # off-topic: the page never actually names the subject
            documents.append(
                {
                    **candidate,
                    "body": text,
                    "source": "searxng",
                    "source_tier": "free",
                    "doc_type": "article",
                    "full_text": bool(body),
                }
            )
        return documents


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:  # noqa: BLE001
        return ""


def _canonical_url(url: str) -> str:
    """Strip scheme/query noise so the same page found by several engines
    dedupes to one document."""
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return f"{host}{parsed.path.rstrip('/')}"
    except Exception:  # noqa: BLE001
        return url


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t[:10], "%Y-%m-%d"),
    ):
        try:
            parsed = parse(text)
            return parsed.replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            continue
    return None

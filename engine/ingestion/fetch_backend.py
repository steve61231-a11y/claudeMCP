"""Tiered page fetching — plain HTTP first, Scrapling when the site says no.

Kenyan newsrooms (Nation, Standard, Star, Citizen) and most social endpoints
sit behind Cloudflare or a WAF that profiles the *TLS handshake*, not just the
User-Agent. `requests` has an unmistakable Python fingerprint, so those hosts
answer 403 with a challenge page. Our body-extraction step reads that as "no
article body" and the report loses the deepest source we have: the reporting
itself.

Scrapling (BSD-3, D4Vinci/Scrapling) fixes exactly that layer. Its `Fetcher`
rides curl_cffi and impersonates a real Chrome TLS + HTTP/2 fingerprint, so
the request looks like a browser without being one — no Chromium process, no
per-page second of latency. `StealthyFetcher` goes further (a real patched
browser that can solve Cloudflare interstitials) but needs a browser install,
so it stays opt-in and last.

Order is deliberate: cheapest that works.

  1. `requests` — free, ~0.2s, and enough for the majority of hosts.
  2. Scrapling `Fetcher` — used only when tier 1 came back blocked.
  3. Scrapling `StealthyFetcher` — off unless `enable_scrapling_stealth`.

Every tier is best-effort: an unavailable dependency or a raised exception
demotes to the next tier and finally to an empty result. Nothing here raises
into a run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from engine.config import settings
from engine.ingestion import http

BACKEND_REQUESTS = "requests"
BACKEND_SCRAPLING = "scrapling"
BACKEND_STEALTH = "scrapling_stealth"

# Statuses a bot wall returns. 200 can still be a challenge page, which is what
# the body markers below are for.
BLOCKED_STATUSES = frozenset({401, 403, 405, 406, 409, 429, 503})

# Interstitials served with a 200. Matched against a lowercased prefix of the
# body, so a news article merely *mentioning* Cloudflare is not misread.
_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "cf-browser-verification",
    "cf_chl_opt",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "ddos protection by",
    "access denied",
    "request unsuccessful. incapsula",
    "pardon our interruption",
)

# A challenge page is small. Anything past this is real content even if it
# happens to contain a marker string.
_CHALLENGE_MAX_CHARS = 8000


@dataclass(frozen=True)
class FetchResult:
    """Outcome of one URL fetch. `html` is "" when every tier failed."""

    url: str
    html: str
    status: int | None
    backend: str | None
    blocked: bool

    @property
    def ok(self) -> bool:
        return bool(self.html) and not self.blocked


_counts: Counter[str] = Counter()


def snapshot() -> dict[str, int]:
    """Per-backend tally since the last `reset()` — how many pages each tier
    won, and how many were blocked outright. Surfaced in the run report so a
    thin corpus can be attributed to bot walls rather than guessed at."""
    return dict(_counts)


def reset() -> None:
    _counts.clear()


def looks_blocked(status: int | None, html: str) -> bool:
    """True when this response is a bot wall rather than the page asked for."""
    if status is not None and status in BLOCKED_STATUSES:
        return True
    if not html:
        return True
    if len(html) > _CHALLENGE_MAX_CHARS:
        return False
    head = html[:_CHALLENGE_MAX_CHARS].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _via_requests(url: str, timeout: int) -> FetchResult:
    try:
        resp = http.get(url, timeout=timeout)
    except Exception:
        return FetchResult(url, "", None, BACKEND_REQUESTS, True)
    html = resp.text or ""
    status = resp.status_code
    if status != 200:
        html = ""
    return FetchResult(url, html, status, BACKEND_REQUESTS, looks_blocked(status, html))


def _scrapling_fetcher():
    """Import lazily: scrapling pulls curl_cffi and playwright bindings, and a
    deployment without them must degrade to tier 1 rather than fail to boot."""
    from scrapling.fetchers import Fetcher  # noqa: PLC0415

    return Fetcher


def _via_scrapling(url: str, timeout: int) -> FetchResult:
    try:
        fetcher = _scrapling_fetcher()
        page = fetcher.get(
            url,
            impersonate=settings.scrapling_impersonate,
            timeout=timeout,
            stealthy_headers=True,
            # A bot wall's redirect to a challenge host is itself the answer;
            # following it just buries the status we want to see.
            follow_redirects=True,
        )
    except Exception:
        return FetchResult(url, "", None, BACKEND_SCRAPLING, True)
    status = getattr(page, "status", None)
    html = getattr(page, "html_content", "") or ""
    if status is not None and status != 200:
        html = ""
    return FetchResult(url, html, status, BACKEND_SCRAPLING, looks_blocked(status, html))


def _via_stealth(url: str, timeout: int) -> FetchResult:
    try:
        from scrapling.fetchers import StealthyFetcher  # noqa: PLC0415

        page = StealthyFetcher.fetch(
            url,
            headless=True,
            solve_cloudflare=True,
            network_idle=True,
            timeout=timeout * 1000,  # scrapling's browser tier is in ms
        )
    except Exception:
        return FetchResult(url, "", None, BACKEND_STEALTH, True)
    status = getattr(page, "status", None)
    html = getattr(page, "html_content", "") or ""
    return FetchResult(url, html, status, BACKEND_STEALTH, looks_blocked(status, html))


def fetch_html(url: str, timeout: int = 20) -> FetchResult:
    """Fetch `url`, escalating through the tiers until one is not blocked.

    Returns the last attempt when all of them are, so callers can tell a bot
    wall (`blocked`, with a status) from a dead link. Never raises."""
    result = _via_requests(url, timeout)
    if result.ok:
        _counts[BACKEND_REQUESTS] += 1
        return result

    if settings.enable_scrapling:
        upgraded = _via_scrapling(url, timeout)
        if upgraded.ok:
            _counts[BACKEND_SCRAPLING] += 1
            return upgraded
        result = upgraded

    if settings.enable_scrapling_stealth:
        upgraded = _via_stealth(url, timeout)
        if upgraded.ok:
            _counts[BACKEND_STEALTH] += 1
            return upgraded
        result = upgraded

    _counts["blocked"] += 1
    return result


def availability() -> dict:
    """Which tiers this deploy can actually use, and what each has fetched.

    `enable_scrapling` being true is not the same as scrapling being installed;
    a missing wheel degrades silently to tier 1, which looks identical to a
    site that simply never blocked us. This makes the difference visible."""
    try:
        _scrapling_fetcher()
        scrapling_ready: bool | str = True
    except Exception as exc:  # noqa: BLE001
        scrapling_ready = f"{type(exc).__name__}: {exc}"[:120]

    try:
        from scrapling.fetchers import StealthyFetcher  # noqa: F401,PLC0415

        stealth_ready: bool | str = True
    except Exception as exc:  # noqa: BLE001
        stealth_ready = f"{type(exc).__name__}: {exc}"[:120]

    return {
        "scrapling_enabled": settings.enable_scrapling,
        "scrapling_importable": scrapling_ready,
        "stealth_enabled": settings.enable_scrapling_stealth,
        "stealth_importable": stealth_ready,
        "impersonate": settings.scrapling_impersonate,
        "pages_by_backend": snapshot(),
    }

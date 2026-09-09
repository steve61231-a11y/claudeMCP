import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Per-host pacing.
#
# A live run reported: "news: returned nothing — HTTPError: 429 Too Many
# Requests" against GDELT, on a SINGLE query. The volume was not the problem:
# GDELT meters per IP at roughly a request every five seconds, and this whole
# service is one IP shared by the report path, the issue map, the six-hourly
# scheduler and the ten-minute keep-alive ping. Nothing anywhere enforced a
# gap, so requests arrived whenever they were made and the free news backbone
# — which, with the paid social tier switched off, IS the corpus — returned
# nothing at all.
#
# urllib3's Retry already backs off AFTER a rejection. This stops the
# rejection happening: hold a minimum interval between requests to the same
# host, process-wide, and widen it when a host says no anyway.
# ---------------------------------------------------------------------------

#: Seconds between requests to a host that publishes (or enforces) a limit.
#: Anything not listed is unthrottled — most hosts do not need it and pacing
#: them would only make a run slower for nothing.
HOST_MIN_INTERVAL = {
    "api.gdeltproject.org": 5.0,   # documented: ~1 request / 5s per IP
    "web.archive.org": 2.0,        # CDX is slow and 500s under pressure
    "www.reddit.com": 2.0,
    "newsapi.org": 1.0,
}

_PACE_LOCK = threading.Lock()
_LAST_REQUEST_AT: dict[str, float] = {}
#: Extra spacing learned from being refused. Grows on a 429 and never shrinks
#: within a process: a host's limit does not widen because we would like it to.
_LEARNED_GAP: dict[str, float] = {}
_LEARNED_CEILING = 30.0


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 — pacing must never break a request
        return ""


def _interval_for(host: str) -> float:
    return HOST_MIN_INTERVAL.get(host, 0.0) + _LEARNED_GAP.get(host, 0.0)


def _pace(host: str) -> None:
    """Hold this host's minimum gap, process-wide, before returning."""
    interval = _interval_for(host)
    if interval <= 0:
        return
    while True:
        with _PACE_LOCK:
            now = time.monotonic()
            wait = interval - (now - _LAST_REQUEST_AT.get(host, 0.0))
            if wait <= 0:
                # Claim the slot inside the lock so two threads cannot both
                # decide it is their turn.
                _LAST_REQUEST_AT[host] = now
                return
        time.sleep(min(wait, 5.0))


def _widen(host: str) -> None:
    """This host refused us even at the current pace. Ask more slowly."""
    if not host:
        return
    with _PACE_LOCK:
        current = _LEARNED_GAP.get(host, 0.0)
        _LEARNED_GAP[host] = min(_LEARNED_CEILING, (current * 2) if current else 2.0)


def pacing_state() -> dict:
    """What the process has learned about who is refusing it — so a thin run
    can say "the source was throttled" instead of "nothing was found"."""
    with _PACE_LOCK:
        return {h: round(_interval_for(h), 1) for h in
                set(HOST_MIN_INTERVAL) | set(_LEARNED_GAP)}


def reset_pacing() -> None:
    with _PACE_LOCK:
        _LAST_REQUEST_AT.clear()
        _LEARNED_GAP.clear()


# ASCII, deliberately: HTTP headers are latin-1 and "Mũũgĩ" is not encodable
# in it — sending the accented form raises before the request leaves.
USER_AGENT = (
    "Muugi/1.0 "
    "(+https://github.com/steve61231-a11y/claudemcp; political research) "
    "python-requests"
)


def build_session(total_retries: int = 4, backoff_factor: float = 2.0) -> requests.Session:
    """Session with exponential backoff on transient failures and 429s.

    urllib3's Retry honors Retry-After headers on 429/503 by default, which
    covers both NewsAPI and SocialCrawl rate limiting.

    backoff_factor is 2.0, not 1.0: at 1.0 the four retries waited 0, 2, 4 and
    8 seconds — fourteen in total, which is inside a rate-limit window rather
    than past it, so every attempt was spent in the same blocked minute and
    the call failed having never really retried. At 2.0 it is 0, 4, 8, 16.
    """
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    session = requests.Session()
    # A descriptive User-Agent, because several sources refuse the default one.
    # Wikipedia's API policy explicitly requires identifying the client and
    # returns 403 to bare `python-requests`, which arrives here as an empty
    # result rather than an error — a subject with an obvious article silently
    # contributing nothing to the corpus.
    session.headers.update({"User-Agent": USER_AGENT})
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_shared_session: requests.Session | None = None


def get(url: str, **kwargs) -> requests.Response:
    """GET through a shared retrying session. Connectors call this (as
    `http.get`) so tests can patch one seam instead of requests internals."""
    global _shared_session
    if _shared_session is None:
        _shared_session = build_session()
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    host = _host_of(url)
    _pace(host)
    response = _shared_session.get(url, **kwargs)
    # Refused even at the current pace: slow down for the rest of the process
    # rather than spending every later request discovering the same thing.
    if response.status_code == 429:
        _widen(host)
    return response

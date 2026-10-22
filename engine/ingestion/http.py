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
    # Kenya Law publishes the primary record for free and has no commercial
    # interest in serving us. Being a good guest is the whole price of
    # admission, and the cost of being greedy is losing the source.
    "new.kenyalaw.org": 2.0,
    "kenyalaw.org": 2.0,
    # Resolving a Google News link to the publisher is two requests to
    # Google per article (google_news_links.py). Unmetered as far as anyone
    # has measured, and kept polite so it stays that way.
    "news.google.com": 0.5,
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
        _REFUSED.clear()


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


class HostRefused(requests.exceptions.RequestException):
    """This host has refused this server outright, recently. Not asked again."""


#: Hosts that refused this SERVER, not a request: Reddit answers every search
#: from a cloud address with "403 Blocked", and Kenya Law's bot wall answers
#: "403 Forbidden". Asking again changes nothing — a live report spent a dozen
#: requests per run rediscovering both, and printed every one of them under
#: "Failed". Remembered for a while rather than for ever, because a block can
#: be lifted and a deploy can move to a different address.
_REFUSED: dict[str, tuple[float, str]] = {}
_REFUSAL_HOURS = 6.0


def remember_refusal(url: str, reason: str, hours: float = _REFUSAL_HOURS) -> None:
    """Mark this URL's host as refusing us. Connectors decide when a refusal
    is the host's verdict on this server rather than on one page."""
    host = _host_of(url)
    if not host:
        return
    with _PACE_LOCK:
        _REFUSED[host] = (time.monotonic() + hours * 3600, reason[:300])


def refused(url: str) -> str | None:
    """Why this URL's host is not being asked, or None if it may be."""
    host = _host_of(url)
    with _PACE_LOCK:
        entry = _REFUSED.get(host)
        if entry and entry[0] <= time.monotonic():
            _REFUSED.pop(host, None)
            entry = None
    return entry[1] if entry else None


def refusal_state() -> dict:
    """Which hosts are currently not being asked, and why — for the operator."""
    now = time.monotonic()
    with _PACE_LOCK:
        return {host: {"reason": reason, "minutes_left": round((until - now) / 60)}
                for host, (until, reason) in _REFUSED.items() if until > now}


def post(url: str, **kwargs) -> requests.Response:
    """POST through the same session, pacing and refusal memory as `get`.
    Not retried by the session's policy, which retries only GETs: a POST
    that half-succeeded must not be sent twice."""
    global _shared_session
    reason = refused(url)
    if reason:
        raise HostRefused(f"not asked: {reason}")
    if _shared_session is None:
        _shared_session = build_session()
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    host = _host_of(url)
    _pace(host)
    response = _shared_session.post(url, **kwargs)
    if response.status_code == 429:
        _widen(host)
    return response


def get(url: str, **kwargs) -> requests.Response:
    """GET through a shared retrying session. Connectors call this (as
    `http.get`) so tests can patch one seam instead of requests internals."""
    global _shared_session
    reason = refused(url)
    if reason:
        raise HostRefused(f"not asked: {reason}")
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

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30


def build_session(total_retries: int = 4, backoff_factor: float = 1.0) -> requests.Session:
    """Session with exponential backoff on transient failures and 429s.

    urllib3's Retry honors Retry-After headers on 429/503 by default, which
    covers both NewsAPI and SocialCrawl rate limiting.
    """
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    session = requests.Session()
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
    return _shared_session.get(url, **kwargs)

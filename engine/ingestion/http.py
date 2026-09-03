import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30


# ASCII, deliberately: HTTP headers are latin-1 and "Mũũgĩ" is not encodable
# in it — sending the accented form raises before the request leaves.
USER_AGENT = (
    "Muugi/1.0 "
    "(+https://github.com/steve61231-a11y/claudemcp; political research) "
    "python-requests"
)


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
    return _shared_session.get(url, **kwargs)

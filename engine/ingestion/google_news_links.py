"""A Google News item's link is Google's, not the newspaper's.

Every item the Google News connector stores carries a link of the form
`https://news.google.com/rss/articles/<id>`. Fetching it lands on a Google
page, not the article, so trafilatura finds no body and the item enters the
corpus as a headline. Measured on 22 October 2026 from GitHub's network
(`scripts/article_probe.py`): **0 of 15** Google News links gave an article
body, and every fetch landed on news.google.com. Google News is the largest
news source in the corpus, so most of the journalism a report "read" was
headlines.

The link can be resolved to the publisher's own URL in two steps — the
article page carries a signature and a timestamp (`data-n-a-sg`,
`data-n-a-ts`), and posting them with the article id to Google's
`batchexecute` endpoint returns the URL. The same probe: **15 of 15**
decoded this way, and **11 of 15** then gave the full article (median about
2,400 characters). The `googlenewsdecoder` library, which wraps the same
idea, decoded 0 of 15, so this is done here, where a failure can say which
step broke.

Resolved URLs are cached for the life of the process: the same story comes
back from several queries in one run and from the next run.
"""

from __future__ import annotations

import json
import re
import threading
from urllib.parse import quote_plus, urlsplit

from engine.ingestion import http

BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

#: Google serves the signature attributes to a browser; the probe that
#: measured this used one, so the decode does too. The rest of the engine
#: keeps its descriptive User-Agent.
BROWSER_HEADERS = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0 Safari/537.36")}

_ARTICLE_ID = re.compile(r"/articles/([A-Za-z0-9_-]+)")
_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP = re.compile(r'data-n-a-ts="([^"]+)"')

_CACHE: dict[str, str | None] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 5000

#: How each decode attempt ended this process, for the run's own account of
#: how much of its news was read in full.
_OUTCOMES: dict[str, int] = {}


def is_google_news(url: str | None) -> bool:
    try:
        return (urlsplit(url or "").hostname or "").lower() == "news.google.com"
    except ValueError:
        return False


def article_id(url: str) -> str | None:
    match = _ARTICLE_ID.search(urlsplit(url).path)
    return match.group(1) if match else None


def _count(outcome: str) -> None:
    with _CACHE_LOCK:
        _OUTCOMES[outcome] = _OUTCOMES.get(outcome, 0) + 1


def outcomes() -> dict[str, int]:
    with _CACHE_LOCK:
        return dict(_OUTCOMES)


def reset() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _OUTCOMES.clear()


def _request_body(aid: str, timestamp: int, signature: str) -> str:
    inner = ["garturlreq",
             [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
               None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
             aid, timestamp, signature]
    # Exactly the form scripts/article_probe.py verified, 15 of 15.
    payload = [[["Fbv4je", json.dumps(inner)]]]
    return f"f.req={quote_plus(json.dumps(payload))}"


def _parse(reply: str) -> str | None:
    """The publisher URL from a batchexecute reply, or None. The reply is a
    guard line, a blank line, then JSON whose third field is itself JSON."""
    try:
        body = reply.split("\n\n", 1)[1]
        url = json.loads(json.loads(body)[0][2])[1]
    except Exception:  # noqa: BLE001 — any other shape is "not decoded"
        return None
    return url if isinstance(url, str) and url.startswith("http") else None


def decode(url: str) -> str | None:
    """The publisher's URL for a Google News link; any other URL unchanged.

    None when it cannot be resolved — the caller keeps the item as a
    headline and says so, rather than fetching Google's page as if it were
    the article. Never raises."""
    if not is_google_news(url):
        return url
    aid = article_id(url)
    if not aid:
        _count("no article id")
        return None
    with _CACHE_LOCK:
        if aid in _CACHE:
            return _CACHE[aid]

    resolved: str | None = None
    try:
        page = http.get(f"https://news.google.com/rss/articles/{aid}",
                        headers=BROWSER_HEADERS, timeout=15)
        signature, timestamp = _SIGNATURE.search(page.text), _TIMESTAMP.search(page.text)
        if not (signature and timestamp):
            _count(f"page HTTP {page.status_code} without a signature")
        else:
            reply = http.post(BATCH_URL, data=_request_body(aid, int(timestamp.group(1)),
                                                            signature.group(1)),
                              headers={**BROWSER_HEADERS, "Content-Type":
                                       "application/x-www-form-urlencoded;charset=UTF-8"},
                              timeout=15)
            resolved = _parse(reply.text)
            _count("decoded" if resolved else f"reply HTTP {reply.status_code} unparseable")
    except Exception as exc:  # noqa: BLE001 — a failed decode is a headline, not a crash
        _count(f"error {type(exc).__name__}")

    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[aid] = resolved
    return resolved

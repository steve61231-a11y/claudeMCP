"""Reddit ingestion via the public JSON endpoints — free, keyless.

Reddit exposes search and listings as JSON with no API key
(`/search.json?q=...`). r/Kenya and the wider site carry real, current opinion
on Kenyan politics. We do a site-wide search plus an r/Kenya-scoped search and
map posts to IngestedMention(platform="reddit", source_type="post").

Reddit rate-limits by User-Agent, so we send a descriptive one. Best-effort:
any failure degrades to an empty list like every other connector.

Keyless is a laptop-only arrangement. From a cloud server Reddit answers every
search with "403 Blocked" — a live report listed it under "Failed" with the
full request URL, twice per query, every run. So:

  * with REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET (a free "script" app), the
    connector signs in app-only and asks oauth.reddit.com, which serves cloud
    servers;
  * without them, the first refusal is remembered (`http.remember_refusal`)
    and the source is not asked again for a few hours. One request learns
    what a dozen used to.
"""

import threading
import time
from datetime import datetime

import requests

from engine.config import settings
from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

SEARCH_URL = "https://www.reddit.com/search.json"
SUB_SEARCH_URL = "https://www.reddit.com/r/{sub}/search.json"
OAUTH_SEARCH_URL = "https://oauth.reddit.com/search"
OAUTH_SUB_SEARCH_URL = "https://oauth.reddit.com/r/{sub}/search"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
KENYA_SUBS = ["Kenya"]
HEADERS = {"User-Agent": "zenith-intel/1.0 (political sentiment research)"}
LIMIT = 50

_TOKEN_LOCK = threading.Lock()
_TOKEN: dict = {}


def _oauth_token() -> str | None:
    """An app-only bearer token, cached until shortly before it expires.

    None when no credentials are configured or Reddit will not issue one —
    the caller then falls back to the keyless endpoint."""
    client_id, secret = settings.reddit_client_id, settings.reddit_client_secret
    if not (client_id and secret):
        return None
    with _TOKEN_LOCK:
        if _TOKEN.get("value") and _TOKEN.get("expires", 0) > time.time() + 60:
            return _TOKEN["value"]
        try:
            resp = requests.post(TOKEN_URL, auth=(client_id, secret),
                                 data={"grant_type": "client_credentials"},
                                 headers=HEADERS, timeout=20)
            resp.raise_for_status()
            body = resp.json()
        except Exception:  # noqa: BLE001 — a token we cannot get is a keyless run
            return None
        token = body.get("access_token")
        if not token:
            return None
        _TOKEN.update(value=token, expires=time.time() + float(body.get("expires_in") or 3600))
        return token


class RedditConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime,
        window_end: datetime, query: str | None = None
    ) -> list[IngestedMention]:
        """Search Reddit for one query.

        `query` is the one this task was planned to run — see
        `orchestrator.KEYWORD_CONNECTORS`. Without it every task for this
        source re-derived the same bare-name phrase, so a twelve-variant
        expansion made twelve identical requests.
        """
        query = query or politician_name
        mentions: list[IngestedMention] = []
        seen: set[str] = set()

        token = _oauth_token()
        search_url = OAUTH_SEARCH_URL if token else SEARCH_URL
        sub_url = OAUTH_SUB_SEARCH_URL if token else SUB_SEARCH_URL
        headers = {**HEADERS, **({"Authorization": f"bearer {token}"} if token else {})}

        requests_to_make = [
            (search_url, {"q": query, "sort": "new", "limit": str(LIMIT), "t": "year"}),
        ]
        for sub in KENYA_SUBS:
            requests_to_make.append(
                (sub_url.format(sub=sub),
                 {"q": query, "restrict_sr": "1", "sort": "new", "limit": str(LIMIT), "t": "year"})
            )

        for url, params in requests_to_make:
            try:
                resp = http.get(url, params=params, headers=headers, timeout=25)
                if resp.status_code == 403 and not token:
                    # Reddit's verdict on this SERVER, not on this query.
                    http.remember_refusal(url, (
                        "Reddit refuses keyless requests from this server (HTTP 403). "
                        "Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET from a free "
                        "'script' app at reddit.com/prefs/apps to restore it."))
                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
            except Exception as exc:  # noqa: BLE001
                self.last_error = (http.refused(url) or f"{type(exc).__name__}: {exc}")[:200]
                if http.refused(url):
                    break   # the next request goes to the same host
                continue
            for child in children:
                mapped = self._map_post(child.get("data", {}), seen, window_start, window_end)
                if mapped:
                    mentions.append(mapped)
        return mentions

    @staticmethod
    def _map_post(d: dict, seen: set, window_start: datetime, window_end: datetime) -> IngestedMention | None:
        pid = str(d.get("id") or "")
        title = (d.get("title") or "").strip()
        body = (d.get("selftext") or "").strip()
        text = (f"{title}\n\n{body}").strip() if body else title
        if not pid or not text or pid in seen:
            return None
        seen.add(pid)
        created = d.get("created_utc")
        # An unparseable date is NOT a date. The window-end fallback keeps
        # ordering working and stamps every undated item onto one day, so
        # the volume chart drew a spike that never happened. Say which it is.
        _known = True
        try:
            posted = datetime.utcfromtimestamp(float(created)) if created else window_end
            _known = bool(created)
        except (TypeError, ValueError, OSError):
            posted = window_end
            _known = False
        posted = min(max(posted, window_start), window_end)
        permalink = d.get("permalink")
        return IngestedMention(
            platform="reddit",
            source_type="post",
            author_handle=str(d.get("author") or "unknown"),
            text=text[:4000],
            posted_at=posted,
            engagement={
                "likes": int(d.get("score") or 0),
                "comments": int(d.get("num_comments") or 0),
            },
            raw_payload={
                "date_estimated": not _known,
                "url": f"https://www.reddit.com{permalink}" if permalink else d.get("url"),
                "subreddit": d.get("subreddit"),
                "post_id": pid,
                "source": "reddit",
            },
        )

"""Reddit ingestion via the public JSON endpoints — free, keyless.

Reddit exposes search and listings as JSON with no API key
(`/search.json?q=...`). r/Kenya and the wider site carry real, current opinion
on Kenyan politics. We do a site-wide search plus an r/Kenya-scoped search and
map posts to IngestedMention(platform="reddit", source_type="post").

Reddit rate-limits by User-Agent, so we send a descriptive one. Best-effort:
any failure degrades to an empty list like every other connector.
"""

from datetime import datetime

from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

SEARCH_URL = "https://www.reddit.com/search.json"
SUB_SEARCH_URL = "https://www.reddit.com/r/{sub}/search.json"
KENYA_SUBS = ["Kenya"]
HEADERS = {"User-Agent": "zenith-intel/1.0 (political sentiment research)"}
LIMIT = 50


class RedditConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        query = politician_name
        mentions: list[IngestedMention] = []
        seen: set[str] = set()

        requests_to_make = [
            (SEARCH_URL, {"q": query, "sort": "new", "limit": str(LIMIT), "t": "year"}),
        ]
        for sub in KENYA_SUBS:
            requests_to_make.append(
                (SUB_SEARCH_URL.format(sub=sub),
                 {"q": query, "restrict_sr": "1", "sort": "new", "limit": str(LIMIT), "t": "year"})
            )

        for url, params in requests_to_make:
            try:
                resp = http.get(url, params=params, headers=HEADERS, timeout=25)
                resp.raise_for_status()
                children = resp.json().get("data", {}).get("children", [])
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"[:200]
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
        try:
            posted = datetime.utcfromtimestamp(float(created)) if created else window_end
        except (TypeError, ValueError, OSError):
            posted = window_end
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
                "url": f"https://www.reddit.com{permalink}" if permalink else d.get("url"),
                "subreddit": d.get("subreddit"),
                "post_id": pid,
                "source": "reddit",
            },
        )

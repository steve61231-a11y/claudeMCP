"""X/Twitter ingestion via Scweet (github.com/Altimis/Scweet).

Scweet scrapes tweets, profiles and followers from X with no paid API — it
drives a headless browser and supports multi-account pooling and proxies. We run
it ALONGSIDE the twscrape connector (different scraping backends, same shared
burner-account credentials) so X coverage survives one backend rate-limiting or
breaking: whichever returns results feeds the same corpus.

X blocks anonymous reads, so a burner login is required (settings.x_username /
x_email / x_password). The account pool + open egress are runtime concerns
absent in the build sandbox, so the fetch→IngestedMention mapping is unit-tested
against fixture rows and the live scrape lights up on deploy.

The concrete Scweet call is wrapped defensively and the scraper is injectable, so
a Scweet version/field rename degrades to an empty result (best-effort, like
every other connector) rather than crashing a run.
"""

from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector


class ScweetConnector(IngestionConnector):
    def __init__(self, scraper=None):
        # `scraper` is injectable for tests. It must expose `.scrape(...)`
        # returning an iterable of row-dicts (Scweet returns tweet records).
        self._scraper = scraper

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        if not settings.enable_scweet:
            return []
        scraper = self._scraper or self._build_scraper()
        if scraper is None:
            return []

        terms = [politician_name, *[a for a in aliases if a]][:3]
        since = window_start.strftime("%Y-%m-%d")
        until = window_end.strftime("%Y-%m-%d")
        limit = settings.scweet_max_tweets

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for term in terms:
            try:
                rows = scraper.scrape(words=[term], since=since, until=until, limit=limit)
            except Exception:
                continue
            for row in rows or []:
                mapped = self._map_row(row, seen, window_start, window_end)
                if mapped:
                    mentions.append(mapped)
        return mentions

    def _build_scraper(self):
        """Construct a real Scweet instance from the shared burner creds. Returns
        None if Scweet isn't installed or no credentials are configured."""
        if not (settings.x_username and settings.x_password):
            return None
        try:
            from Scweet.scweet import Scweet
        except Exception:
            return None
        try:
            return Scweet(
                username=settings.x_username,
                email=settings.x_email or None,
                password=settings.x_password,
                headless=True,
            )
        except Exception:
            return None

    @staticmethod
    def _get(row, *keys):
        """Read the first present key from a dict-or-object row (Scweet's column
        names vary by version), defensively."""
        for k in keys:
            if isinstance(row, dict):
                if k in row and row[k] not in (None, ""):
                    return row[k]
            else:
                v = getattr(row, k, None)
                if v not in (None, ""):
                    return v
        return None

    @classmethod
    def _map_row(cls, row, seen: set, window_start: datetime, window_end: datetime) -> IngestedMention | None:
        tid = str(cls._get(row, "tweetId", "tweet_id", "id", "TweetURL", "Tweet URL") or "")
        text = cls._get(row, "Text", "text", "Embedded_text", "embedded_text", "content") or ""
        text = str(text).strip()
        if not text:
            return None
        key = tid or text[:80]
        if key in seen:
            return None
        seen.add(key)

        handle = str(cls._get(row, "UserScreenName", "username", "user", "handle") or "unknown").lstrip("@")
        posted = cls._parse_ts(cls._get(row, "Timestamp", "timestamp", "date", "posted_at"))
        if posted is None:
            posted = window_end
        posted = min(max(posted, window_start), window_end)

        def _int(v):
            try:
                return int(str(v).replace(",", "")) if v not in (None, "") else 0
            except (TypeError, ValueError):
                return 0

        url = cls._get(row, "TweetURL", "Tweet URL", "url", "link")
        return IngestedMention(
            platform="x",
            source_type="post",
            author_handle=handle,
            text=text,
            posted_at=posted,
            engagement={
                "likes": _int(cls._get(row, "Likes", "likes")),
                "shares": _int(cls._get(row, "Retweets", "retweets", "shares")),
                "comments": _int(cls._get(row, "Comments", "comments", "replies")),
            },
            raw_payload={
                "url": url,
                "tweet_id": tid or None,
                "source": "scweet",
            },
        )

    @staticmethod
    def _parse_ts(value) -> datetime | None:
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if not value:
            return None
        s = str(value).strip().replace("T", " ").replace("Z", "")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[: len(fmt) + 2], fmt)
            except ValueError:
                continue
        return None

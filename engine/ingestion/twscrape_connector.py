"""X/Twitter ingestion via twscrape (github.com/vladkens/twscrape).

X is our most expensive SocialCrawl surface and the most rate-limited direct
API. twscrape scrapes search results using a pool of logged-in accounts with
automatic rotation and rate-limit handling — no paid API. This gives free,
scalable X coverage as a complement (or fallback) to SocialCrawl's twitter
endpoint.

twscrape is async and reads its account pool from a sqlite db that the
operator sets up once (`twscrape add_accounts` / `login_accounts`). This
connector wraps the async search in a sync entry point matching the pipeline
interface; the account pool + open egress are required at runtime (both absent
in the build sandbox, so mapping is unit-tested against fixture tweets).
"""

import asyncio
from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector

TWEETS_PER_TERM = 80


class TwscrapeConnector(IngestionConnector):
    def __init__(self, api=None):
        # `api` is injectable for tests; real one is twscrape.API() over the
        # operator's account-pool db.
        self._api = api

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        try:
            return asyncio.run(self._fetch_async(politician_name, aliases, window_start, window_end))
        except Exception:
            return []

    async def _fetch_async(self, politician_name, aliases, window_start, window_end) -> list[IngestedMention]:
        api = self._api
        if api is None:
            from twscrape import API

            api = API(settings.twscrape_db or "accounts.db")

        terms = [politician_name, *[a for a in aliases if a]][:3]
        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for term in terms:
            # X search operators give us a windowed, language-filtered query.
            query = (
                f'{term} since:{window_start.strftime("%Y-%m-%d")} '
                f'until:{window_end.strftime("%Y-%m-%d")}'
            )
            try:
                async for tweet in api.search(query, limit=TWEETS_PER_TERM):
                    mapped = self._map_tweet(tweet, seen)
                    if mapped:
                        mentions.append(mapped)
            except Exception:
                continue
        return mentions

    @staticmethod
    def _map_tweet(tweet, seen: set) -> IngestedMention | None:
        # twscrape Tweet: .id, .rawContent, .user.username, .likeCount,
        # .retweetCount, .replyCount, .date, .url. Access defensively so a
        # library field rename degrades to a skip, not a crash.
        tid = str(getattr(tweet, "id", "") or "")
        if not tid or tid in seen:
            return None
        text = getattr(tweet, "rawContent", None) or getattr(tweet, "content", None) or ""
        if not text:
            return None
        seen.add(tid)
        user = getattr(tweet, "user", None)
        handle = getattr(user, "username", None) or "unknown"
        posted = getattr(tweet, "date", None)
        if not isinstance(posted, datetime):
            posted = datetime.utcnow()
        elif posted.tzinfo is not None:
            posted = posted.replace(tzinfo=None)
        return IngestedMention(
            platform="twitter",
            source_type="post",
            author_handle=handle,
            text=text,
            posted_at=posted,
            engagement={
                "likes": getattr(tweet, "likeCount", 0) or 0,
                "shares": getattr(tweet, "retweetCount", 0) or 0,
                "comments": getattr(tweet, "replyCount", 0) or 0,
                "views": getattr(tweet, "viewCount", 0) or 0,
            },
            raw_payload={
                "url": getattr(tweet, "url", None),
                "tweet_id": tid,
                "followers": getattr(user, "followersCount", None),
                "source": "twscrape",
            },
        )

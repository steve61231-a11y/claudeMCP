from datetime import datetime, timedelta

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector
from engine.ingestion import http

NEWSAPI_URL = "https://newsapi.org/v2/everything"


class NewsApiConnector(IngestionConnector):
    """Real ingestion source backed by NewsAPI.org.

    Direct scraping of X/Facebook/LinkedIn requires either official platform
    API access or a paid third-party connector (SocialCrawl, Bright Data —
    see plan notes); neither is configured here. NewsAPI covers public
    reporting and quoted social posts, which is a legitimate, ToS-compliant
    real-data source that needs only an API key (`NEWSAPI_KEY`) to run
    unattended, unlike one-off manual web search.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.newsapi_key
        if not self.api_key:
            raise RuntimeError("NEWSAPI_KEY is not configured")

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        query = " OR ".join(f'"{name}"' for name in [politician_name, *aliases])
        mentions: list[IngestedMention] = []
        page = 1
        while True:
            page_mentions, has_more = self.search_page(query, page, window_start, window_end)
            mentions.extend(page_mentions)
            if not has_more:
                break
            page += 1
        return mentions

    def search_page(
        self, query: str, page: int, window_start: datetime, window_end: datetime
    ) -> tuple[list[IngestedMention], bool]:
        """One page of NewsAPI results; returns (mentions, has_more)."""
        # NewsAPI's free tier only searches ~30 days back and returns
        # 426 Upgrade Required for older `from` dates — clamp instead of fail.
        window_start = max(window_start, window_end - timedelta(days=28))
        response = http.get(
            NEWSAPI_URL,
            params={
                "q": query,
                "from": window_start.date().isoformat(),
                "to": window_end.date().isoformat(),
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 100,
                "page": page,
            },
            headers={"X-Api-Key": self.api_key},
            timeout=15,
        )
        response.raise_for_status()
        body = response.json()
        articles = body.get("articles", [])
        mentions: list[IngestedMention] = []
        for article in articles:
            posted_at = datetime.fromisoformat(article["publishedAt"].replace("Z", "+00:00")).replace(tzinfo=None)
            text = " ".join(filter(None, [article.get("title"), article.get("description"), article.get("content")]))
            mentions.append(
                IngestedMention(
                    platform="news",
                    source_type="article",
                    author_handle=(article.get("source") or {}).get("name", "unknown"),
                    text=text,
                    posted_at=posted_at,
                    engagement={},
                    raw_payload=article,
                )
            )
        total_results = body.get("totalResults", 0)
        has_more = bool(articles) and page * 100 < total_results
        return mentions, has_more

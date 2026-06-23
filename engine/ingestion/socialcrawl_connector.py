from datetime import datetime

import requests

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev"
SOCIALCRAWL_BRAND_MENTIONS_PATH = "/v1/prism/brand-mentions"


class SocialCrawlConnector(IngestionConnector):
    """Real ingestion source backed by the SocialCrawl API (prism/brand-mentions).

    `recent_mentions` items are web-content-analysis hits (news/blog pages
    mentioning the keyword), not native social posts: there's no
    platform/author/engagement field, just url/domain, a snippet, a
    `fetch_time` timestamp, and a per-item `sentiment`/`connotation` score
    breakdown. The mapping below is based on a confirmed live response body.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.socialcrawl_api_key
        if not self.api_key:
            raise RuntimeError("SOCIALCRAWL_API_KEY is not configured")

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        response = requests.get(
            f"{SOCIALCRAWL_BASE_URL}{SOCIALCRAWL_BRAND_MENTIONS_PATH}",
            params={
                "keyword": politician_name,
                "date_from": window_start.date().isoformat(),
                "date_to": window_end.date().isoformat(),
            },
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            error = body.get("error", {})
            raise RuntimeError(f"SocialCrawl error ({error.get('type')}): {error.get('message')}")

        data = body.get("data", body)
        recent_mentions = data.get("recent_mentions") or data.get("mentions") or []

        mentions: list[IngestedMention] = []
        for item in recent_mentions:
            posted_at_raw = item.get("date_published") or item.get("fetch_time") or item.get("posted_at")
            posted_at = self._parse_datetime(posted_at_raw) if posted_at_raw else window_end

            content_info = (item.get("_raw") or {}).get("content_info") or {}
            engagement = item.get("engagement") or (item.get("_raw") or {}).get("social_metrics") or {}

            mentions.append(
                IngestedMention(
                    platform=item.get("platform") or item.get("domain") or "web",
                    source_type=item.get("source_type") or self._infer_source_type(item.get("page_types")),
                    author_handle=item.get("author") or content_info.get("author") or item.get("domain") or "unknown",
                    text=item.get("text") or item.get("snippet") or content_info.get("snippet") or "",
                    posted_at=posted_at,
                    engagement=engagement,
                    raw_payload=item,
                )
            )
        return mentions

    @staticmethod
    def _infer_source_type(page_types: list[str] | None) -> str:
        if not page_types:
            return "web_page"
        if "news" in page_types:
            return "article"
        if "blogs" in page_types:
            return "blog_post"
        return page_types[0]

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            # SocialCrawl's `fetch_time` uses "YYYY-MM-DD HH:MM:SS +00:00" (space, no "T").
            return datetime.strptime(value.split(" +")[0].split(" -")[0], "%Y-%m-%d %H:%M:%S")

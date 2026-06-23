from datetime import datetime

import requests

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev"
SOCIALCRAWL_BRAND_MENTIONS_PATH = "/v1/prism/brand-mentions"


class SocialCrawlConnector(IngestionConnector):
    """Real ingestion source backed by the SocialCrawl API (prism/brand-mentions).

    Field names for individual mentions in the response are inferred from
    SocialCrawl's published endpoint description (mention volume time-series,
    sentiment split, top sources, recent mentions) rather than a confirmed
    live response body — `www.socialcrawl.dev` was not reachable from this
    sandbox's network egress allowlist at the time this was written. Access
    is defensive (`.get()` with fallbacks) so a few mismatched field names
    degrade rather than crash; re-verify against a real response once egress
    is available and tighten the mapping if needed.
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
            posted_at_raw = item.get("posted_at") or item.get("published_at") or item.get("date")
            posted_at = self._parse_datetime(posted_at_raw) if posted_at_raw else window_end

            engagement = item.get("engagement") or {
                "likes": item.get("likes", 0),
                "shares": item.get("shares", 0),
                "comments": item.get("comments", 0),
            }

            mentions.append(
                IngestedMention(
                    platform=item.get("platform", "unknown"),
                    source_type=item.get("source_type", "social_post"),
                    author_handle=item.get("author") or item.get("author_handle") or item.get("username") or "unknown",
                    text=item.get("text") or item.get("snippet") or item.get("content") or "",
                    posted_at=posted_at,
                    engagement=engagement,
                    raw_payload=item,
                )
            )
        return mentions

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

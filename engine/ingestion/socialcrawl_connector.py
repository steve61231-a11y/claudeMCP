from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector
from engine.ingestion import http

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev"
SOCIALCRAWL_BRAND_MENTIONS_PATH = "/v1/prism/brand-mentions"


class SocialCrawlConnector(IngestionConnector):
    """Real ingestion source backed by the SocialCrawl API.

    Combines several distinct SocialCrawl data surfaces into one merged
    mention list, so the rest of the pipeline (Postgres schema, report
    generator, influence scoring) never has to know which surface a given
    mention came from:

    - Brand mentions (`prism/brand-mentions`): web/news/blog content-analysis
      hits — domains, article snippets, page-level sentiment. No native
      social posts, handles, or engagement.
    - Discovery search (`_fetch_discovery`): keyword-based native social
      search across TikTok, YouTube, LinkedIn and Twitter — finds who is
      talking about the politician without needing a known handle.
    - Profile activity (`_fetch_profile_activity`): handle/URL-based
      tracking of a specific person's own posts (their official accounts,
      or a tracked rival's), used for competitive-intelligence comparisons.

    Each surface is fetched independently and wrapped in its own
    try/except: a failure on one platform (credits exhausted, rate limit,
    no data) must not drop mentions already gathered from the others.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.socialcrawl_api_key
        if not self.api_key:
            raise RuntimeError("SOCIALCRAWL_API_KEY is not configured")

    # Platforms with a top-level comment endpoint reachable from a discovery
    # search result (post/video id), used to pull grassroots reaction data
    # for the most-engaged items found during discovery.
    _COMMENT_ENDPOINTS = {
        "tiktok": "/v1/tiktok/post/comments",
        "youtube": "/v1/youtube/video/comments",
    }

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        mentions: list[IngestedMention] = []
        mentions.extend(self._fetch_brand_mentions(politician_name, window_start, window_end))
        mentions.extend(self._fetch_discovery(politician_name, aliases, window_start, window_end))
        return mentions

    def discover_handles(self, politician_name: str) -> dict[str, list[str]]:
        """Best-effort handle discovery for politicians we don't yet have a
        known profile/handle for. Returns platform -> candidate handles,
        for the caller (or operator) to confirm before they're persisted to
        `Politician.social_handles` and used with `fetch_profile_activity`.
        """
        candidates: dict[str, list[str]] = {}
        try:
            candidates["tiktok"] = self._discover_tiktok_users(politician_name)
        except Exception:
            candidates["tiktok"] = []
        try:
            candidates["youtube"] = self._discover_youtube_channels(politician_name)
        except Exception:
            candidates["youtube"] = []
        return candidates

    def _discover_tiktok_users(self, query: str) -> list[str]:
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}/v1/tiktok/search/users",
            params={"query": query},
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return []
        data = body.get("data", body)
        users = data.get("results") or data.get("items") or data.get("users") or []
        return [u.get("username") or u.get("handle") for u in users if isinstance(u, dict) and (u.get("username") or u.get("handle"))]

    def _discover_youtube_channels(self, query: str) -> list[str]:
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}/v1/youtube/search",
            params={"query": query, "type": "channel"},
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return []
        data = body.get("data", body)
        channels = data.get("results") or data.get("items") or data.get("videos") or []
        return [
            c.get("channel_id") or c.get("channel_url") or c.get("channel_name")
            for c in channels
            if isinstance(c, dict) and (c.get("channel_id") or c.get("channel_url") or c.get("channel_name"))
        ]

    def fetch_profile_activity(
        self, handles: dict[str, str], window_start: datetime, window_end: datetime, source_type: str = "post"
    ) -> list[IngestedMention]:
        """Fetch a specific person's own activity from their known handles/URLs.

        `handles` maps platform name (tiktok|youtube|facebook|linkedin|twitter)
        to that platform's handle or profile URL. Used both for a politician's
        own official accounts and for tracked rivals (competitive intelligence),
        with the caller responsible for tagging rival results with a
        distinguishing `source_type` (e.g. "rival_activity").
        """
        mentions: list[IngestedMention] = []
        for platform, handle in handles.items():
            if not handle:
                continue
            fetcher = self._PROFILE_FETCHERS.get(platform)
            if not fetcher:
                continue
            try:
                mentions.extend(fetcher(self, handle, window_start, window_end, source_type))
            except Exception:
                continue
        return mentions

    def _fetch_brand_mentions(
        self, politician_name: str, window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        response = http.get(
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

    def _fetch_discovery(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        """Keyword-based native social search — finds posts about the
        politician without needing a known handle, across the platforms
        SocialCrawl supports keyword search on directly.
        """
        mentions: list[IngestedMention] = []
        discovery_calls = [
            ("tiktok", "/v1/tiktok/search", "video"),
            ("tiktok", "/v1/tiktok/search/hashtag", "video"),
            ("youtube", "/v1/youtube/search", "video"),
            ("youtube", "/v1/youtube/search/hashtag", "video"),
            ("linkedin", "/v1/linkedin/search/posts", "post"),
            ("twitter", "/v1/twitter/ai-search", "post"),
        ]
        for platform, path, default_source_type in discovery_calls:
            try:
                posts = self._call_search_endpoint(platform, path, default_source_type, politician_name)
            except Exception:
                continue
            mentions.extend(posts)
            comment_endpoint = self._COMMENT_ENDPOINTS.get(platform)
            if not comment_endpoint:
                continue
            for post in posts:
                try:
                    mentions.extend(self._fetch_comments_for_post(platform, comment_endpoint, post))
                except Exception:
                    continue
        return mentions

    def _call_search_endpoint(
        self, platform: str, path: str, default_source_type: str, query: str
    ) -> list[IngestedMention]:
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{path}",
            params={"query": query},
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return []

        data = body.get("data", body)
        items = data.get("results") or data.get("items") or data.get("posts") or data.get("videos") or []

        mentions: list[IngestedMention] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mentions.append(self._map_social_item(platform, default_source_type, item))
        return mentions

    def _fetch_comments_for_post(
        self, platform: str, comment_endpoint: str, post: IngestedMention
    ) -> list[IngestedMention]:
        """Pulls grassroots reaction data for a discovered post — comments
        are treated as core ingestion signal (not optional) since they
        often carry the actual sentiment/narrative, distinct from the
        post text itself.
        """
        post_payload = post.get("raw_payload") or {}
        post_id = post_payload.get("id") or post_payload.get("video_id") or post_payload.get("post_id")
        if not post_id:
            return []

        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{comment_endpoint}",
            params={"id": post_id, "video_id": post_id, "post_id": post_id},
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return []

        data = body.get("data", body)
        comments = data.get("results") or data.get("items") or data.get("comments") or []

        mentions: list[IngestedMention] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            mentions.append(self._map_social_item(platform, "comment", comment))
        return mentions

    def _fetch_profile_video_posts(
        self, handle: str, window_start: datetime, window_end: datetime, source_type: str
    ) -> list[IngestedMention]:
        return self._call_profile_endpoint("tiktok", "/v1/tiktok/profile/videos", source_type, handle)

    def _fetch_channel_videos(
        self, handle: str, window_start: datetime, window_end: datetime, source_type: str
    ) -> list[IngestedMention]:
        return self._call_profile_endpoint("youtube", "/v1/youtube/channel/videos", source_type, handle)

    def _fetch_facebook_profile_posts(
        self, handle: str, window_start: datetime, window_end: datetime, source_type: str
    ) -> list[IngestedMention]:
        return self._call_profile_endpoint("facebook", "/v1/facebook/profile/posts", source_type, handle)

    def _fetch_linkedin_company_posts(
        self, handle: str, window_start: datetime, window_end: datetime, source_type: str
    ) -> list[IngestedMention]:
        return self._call_profile_endpoint("linkedin", "/v1/linkedin/company/posts", source_type, handle)

    def _fetch_user_tweets(
        self, handle: str, window_start: datetime, window_end: datetime, source_type: str
    ) -> list[IngestedMention]:
        return self._call_profile_endpoint("twitter", "/v1/twitter/user/tweets", source_type, handle)

    _PROFILE_FETCHERS = {
        "tiktok": _fetch_profile_video_posts,
        "youtube": _fetch_channel_videos,
        "facebook": _fetch_facebook_profile_posts,
        "linkedin": _fetch_linkedin_company_posts,
        "twitter": _fetch_user_tweets,
    }

    def _call_profile_endpoint(
        self, platform: str, path: str, source_type: str, handle: str
    ) -> list[IngestedMention]:
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{path}",
            params={"handle": handle, "url": handle},
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return []

        data = body.get("data", body)
        items = data.get("results") or data.get("items") or data.get("posts") or data.get("videos") or []

        mentions: list[IngestedMention] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            mentions.append(self._map_social_item(platform, source_type, item, author_fallback=handle))
        return mentions

    @staticmethod
    def _map_social_item(
        platform: str, default_source_type: str, item: dict, author_fallback: str | None = None
    ) -> IngestedMention:
        posted_at_raw = (
            item.get("created_at")
            or item.get("posted_at")
            or item.get("published_at")
            or item.get("timestamp")
        )
        posted_at = (
            SocialCrawlConnector._parse_datetime(posted_at_raw) if posted_at_raw else datetime.utcnow()
        )

        engagement = item.get("engagement") or item.get("stats") or item.get("metrics") or {}

        return IngestedMention(
            platform=platform,
            source_type=item.get("source_type") or default_source_type,
            author_handle=(
                item.get("author")
                or item.get("author_handle")
                or item.get("username")
                or item.get("channel_name")
                or author_fallback
                or "unknown"
            ),
            text=item.get("text") or item.get("caption") or item.get("title") or item.get("description") or "",
            posted_at=posted_at,
            engagement=engagement,
            raw_payload=item,
        )

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

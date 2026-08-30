from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector
from engine.ingestion import http

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev"
SOCIALCRAWL_BRAND_MENTIONS_PATH = "/v1/prism/brand-mentions"

# Keyword/hashtag search endpoints planned per query variant by the ingestion
# orchestrator. query_kind picks which variant list feeds the endpoint.
SEARCH_ENDPOINTS: list[tuple[str, str, str, str]] = [
    # (platform, path, default_source_type, query_kind: text|hashtag)
    ("tiktok", "/v1/tiktok/search", "video", "text"),
    ("tiktok", "/v1/tiktok/search/hashtag", "video", "hashtag"),
    ("youtube", "/v1/youtube/search", "video", "text"),
    ("youtube", "/v1/youtube/search/hashtag", "video", "hashtag"),
    ("instagram", "/v1/instagram/search/hashtag", "post", "hashtag"),
    ("instagram", "/v1/instagram/search/reels", "video", "text"),
    ("linkedin", "/v1/linkedin/search/posts", "post", "text"),
    ("twitter", "/v1/twitter/ai-search", "post", "text"),
    ("threads", "/v1/threads/search", "post", "text"),
    ("reddit", "/v1/reddit/search", "post", "text"),
    ("google_news", "/v1/google_news/search", "article", "text"),
    ("google", "/v1/google/search", "web_page", "text"),
]

TRANSCRIPT_ENDPOINTS = {
    "tiktok": "/v1/tiktok/post/transcript",
    "youtube": "/v1/youtube/video/transcript",
}

# Client-side per-request credit estimates for budget guardrails (matches the
# published pricing tiers: searches/comments 1cr, transcripts 10cr,
# brand-mentions composite 20cr).
ENDPOINT_CREDIT_COST = {
    SOCIALCRAWL_BRAND_MENTIONS_PATH: 20.0,
    "/v1/tiktok/post/transcript": 10.0,
    "/v1/youtube/video/transcript": 10.0,
}
DEFAULT_CREDIT_COST = 1.0


def credit_cost(path: str) -> float:
    return ENDPOINT_CREDIT_COST.get(path, DEFAULT_CREDIT_COST)


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
    # search result, used to pull grassroots reaction data for the
    # most-engaged items found during discovery. `key` is which identifier the
    # endpoint takes: tiktok/youtube resolve by post/video id, while
    # instagram/facebook/reddit require the full post URL (per SocialCrawl docs).
    _COMMENT_ENDPOINTS = {
        "tiktok": ("/v1/tiktok/post/comments", "id"),
        "youtube": ("/v1/youtube/video/comments", "id"),
        "instagram": ("/v1/instagram/post/comments", "url"),
        "facebook": ("/v1/facebook/post/comments", "url"),
        "reddit": ("/v1/reddit/post/comments", "url"),
    }

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        mentions: list[IngestedMention] = []
        mentions.extend(self._fetch_brand_mentions(politician_name, window_start, window_end))
        mentions.extend(self._fetch_discovery(politician_name, aliases, window_start, window_end))
        return mentions

    def check_balance(self) -> float | None:
        """Remaining credit balance (0cr meta endpoint). None if unreachable —
        callers treat unknown as 'proceed but flag it', never as zero."""
        try:
            response = http.get(
                f"{SOCIALCRAWL_BASE_URL}/v1/credits/balance",
                headers={"x-api-key": self.api_key},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json().get("data", response.json())
            for key in ("balance", "credits", "remaining"):
                if key in data:
                    return float(data[key])
        except Exception:
            pass
        return None

    @staticmethod
    def _search_params(path: str, query: str, page: int, window_start: datetime, window_end: datetime) -> dict:
        """Endpoint-specific query params — verified against SocialCrawl docs.

        Hashtag endpoints require `hashtag` (400 on `query`); google_news
        requires `keyword` with a depth/time_range model and no pagination.
        Everything else takes `query` + best-effort date/page params (ignored
        where unsupported; window filtering also happens client-side).
        """
        if path.endswith("/search/hashtag"):
            return {"hashtag": query.lstrip("#").replace(" ", "")}
        if path == "/v1/google_news/search":
            days = (window_end - window_start).days
            time_range = "year" if days > 31 else "month" if days > 7 else "week"
            return {"keyword": query, "depth": "100", "time_range": time_range}
        return {
            "query": query,
            "page": str(page),
            "date_from": window_start.date().isoformat(),
            "date_to": window_end.date().isoformat(),
        }

    def search_page(
        self,
        platform: str,
        path: str,
        query: str,
        page: int,
        window_start: datetime,
        window_end: datetime,
        default_source_type: str = "post",
        idempotency_key: str | None = None,
    ) -> tuple[list[IngestedMention], bool]:
        """One page of a keyword/hashtag search. Returns (mentions, has_more).

        Date params are passed to every endpoint (ignored where unsupported);
        results outside the window are also filtered client-side since most
        social search endpoints don't honor date ranges. has_more is a
        server-driven signal where available, else "the page was non-empty".
        """
        params = self._search_params(path, query, page, window_start, window_end)
        headers = {"x-api-key": self.api_key}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = http.get(f"{SOCIALCRAWL_BASE_URL}{path}", params=params, headers=headers, timeout=30)
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            error = body.get("error", {})
            raise RuntimeError(f"SocialCrawl error ({error.get('type')}): {error.get('message')}")

        data = body.get("data", body)
        items = data.get("results") or data.get("items") or data.get("posts") or data.get("videos") or []
        mentions = [
            self._map_social_item(platform, default_source_type, item)
            for item in items
            if isinstance(item, dict)
        ]
        has_more = bool(items) and bool(data.get("has_more", True)) and not data.get("is_last_page", False)
        # Endpoints without a page param would just re-serve page 1 forever.
        if path.endswith("/search/hashtag") or path == "/v1/google_news/search":
            has_more = False
        return mentions, has_more

    def comments_page(
        self, platform: str, post_id: str, page: int = 1, idempotency_key: str | None = None
    ) -> tuple[list[IngestedMention], bool]:
        """One page of comments for a discovered post. `post_id` is the
        platform post/video id for tiktok/youtube, or the full post URL for
        instagram/facebook/reddit."""
        entry = self._COMMENT_ENDPOINTS.get(platform)
        if not entry:
            return [], False
        endpoint, key_kind = entry
        headers = {"x-api-key": self.api_key}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if key_kind == "url":
            params = {"url": post_id, "page": str(page)}
        else:
            params = {"id": post_id, "video_id": post_id, "post_id": post_id, "page": str(page)}
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{endpoint}",
            params=params,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return [], False
        data = body.get("data", body)
        comments = data.get("results") or data.get("items") or data.get("comments") or []
        mentions = [
            self._map_social_item(platform, "comment", c) for c in comments if isinstance(c, dict)
        ]
        has_more = bool(comments) and bool(data.get("has_more", True))
        return mentions, has_more

    def fetch_transcript(self, platform: str, post_id: str) -> str | None:
        """Video transcript for a linked, high-engagement video (premium-priced)."""
        endpoint = TRANSCRIPT_ENDPOINTS.get(platform)
        if not endpoint:
            return None
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{endpoint}",
            params={"id": post_id, "video_id": post_id, "post_id": post_id},
            headers={"x-api-key": self.api_key},
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("success", True):
            return None
        data = body.get("data", body)
        transcript = data.get("transcript") or data.get("text")
        if isinstance(transcript, list):
            transcript = " ".join(
                seg.get("text", "") if isinstance(seg, dict) else str(seg) for seg in transcript
            )
        return transcript or None

    @staticmethod
    def extract_author_profile(platform: str, item: dict) -> dict | None:
        """Best-effort author metadata from a raw search/comment item, used to
        maintain author_profiles (follower counts drive influencer detection)."""
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        handle = (
            (author.get("username") or author.get("handle") or author.get("name"))
            if author
            else (item.get("author_handle") or item.get("username") or item.get("channel_name"))
        ) or (item.get("author") if isinstance(item.get("author"), str) else None)
        if not handle:
            return None
        followers = None
        for source in (author, item, item.get("stats") or {}, item.get("author_stats") or {}):
            if not isinstance(source, dict):
                continue
            for key in ("follower_count", "followers", "subscriber_count", "subscribers", "fans"):
                if source.get(key) is not None:
                    try:
                        followers = int(source[key])
                    except (TypeError, ValueError):
                        continue
                    break
            if followers is not None:
                break
        return {
            "platform": platform,
            "handle": str(handle),
            "display_name": (author.get("nickname") or author.get("display_name")) if author else None,
            "follower_count": followers,
            "profile_url": (author.get("url") or author.get("profile_url")) if author else None,
        }

    def discover_handles(self, politician_name: str) -> dict[str, list[str]]:
        """Best-effort handle discovery for politicians we don't yet have a
        known profile/handle for. Returns platform -> candidate handles,
        for the caller (or operator) to confirm before they're persisted to
        `Politician.social_handles` and used with `fetch_profile_activity`.
        """
        candidates: dict[str, list[str]] = {}
        try:
            candidates["tiktok"] = self._discover_tiktok_users(politician_name)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"tiktok discovery: {type(exc).__name__}: {exc}"[:200]
            candidates["tiktok"] = []
        try:
            candidates["youtube"] = self._discover_youtube_channels(politician_name)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"youtube discovery: {type(exc).__name__}: {exc}"[:200]
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
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{platform}/{handle}: {type(exc).__name__}: {exc}"[:200]
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
            except Exception as exc:  # noqa: BLE001
                # A paid endpoint refusing us must be visible: this is money
                # spent for nothing, reported as a platform with no chatter.
                self.last_error = f"{platform} search: {type(exc).__name__}: {exc}"[:200]
                continue
            mentions.extend(posts)
            comment_endpoint = self._COMMENT_ENDPOINTS.get(platform)
            if not comment_endpoint:
                continue
            for post in posts:
                try:
                    mentions.extend(self._fetch_comments_for_post(platform, comment_endpoint, post))
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"{platform} comments: {type(exc).__name__}: {exc}"[:200]
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
        post_fields = post_payload.get("post") if isinstance(post_payload.get("post"), dict) else post_payload
        post_id = post_fields.get("id") or post_fields.get("video_id") or post_fields.get("post_id")
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
        # Some search surfaces (TikTok/YouTube as of the latest SocialCrawl
        # schema) wrap the actual post under a `post` envelope alongside a
        # `computed` block (engagement_rate, estimated_reach, etc.) instead
        # of returning post fields at the top level. Unwrap it so field
        # lookups below work for either shape; raw_payload keeps the full
        # original item (including `computed`) for traceability.
        fields = item.get("post") if isinstance(item.get("post"), dict) else item
        content = fields.get("content") if isinstance(fields.get("content"), dict) else {}

        posted_at_raw = (
            fields.get("created_at")
            or fields.get("posted_at")
            or fields.get("published_at")
            or fields.get("timestamp")
        )
        posted_at = (
            SocialCrawlConnector._parse_datetime(posted_at_raw) if posted_at_raw else datetime.utcnow()
        )

        engagement = (
            fields.get("engagement") or fields.get("stats") or fields.get("metrics") or fields.get("activity") or {}
        )
        # SocialCrawl's TikTok/YouTube schema explicitly nulls unset metrics
        # (e.g. {"likes": null}) rather than omitting the key, which breaks
        # downstream `.get(key, 0)` defaults. Strip nulls so missing-metric
        # handling stays consistent across platforms.
        engagement = {k: v for k, v in engagement.items() if v is not None}

        author = fields.get("author") or fields.get("author_handle") or fields.get("username") or fields.get("channel_name")
        if isinstance(author, dict):
            author = author.get("username") or author.get("handle") or author.get("name") or author.get("url")

        text = (
            fields.get("text")
            or fields.get("caption")
            or fields.get("title")
            or fields.get("description")
            or content.get("text")
            or content.get("caption")
            or ""
        )

        return IngestedMention(
            platform=platform,
            source_type=fields.get("source_type") or default_source_type,
            author_handle=author or author_fallback or "unknown",
            text=text,
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
    def _parse_datetime(value: str | int | float) -> datetime:
        # TikTok/Reddit return unix epochs (create_time/created_utc), seconds
        # or milliseconds, sometimes as numeric strings.
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            epoch = float(value)
            if epoch > 1e12:  # milliseconds
                epoch /= 1000.0
            return datetime.utcfromtimestamp(epoch)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            # SocialCrawl's `fetch_time` uses "YYYY-MM-DD HH:MM:SS +00:00" (space, no "T").
            return datetime.strptime(value.split(" +")[0].split(" -")[0], "%Y-%m-%d %H:%M:%S")

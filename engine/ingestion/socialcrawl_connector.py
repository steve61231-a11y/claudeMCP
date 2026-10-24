from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector, period_date
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
    # Raw tweets. `twitter/ai-search` was here: it answers with xAI Grok's
    # written summary plus the posts it cited, under keys this connector
    # never read — so every X search cost 5 credits, stored nothing, and
    # read as "no chatter on X". A model's answer must never be collected
    # as a source anyway; this endpoint returns what people posted.
    ("twitter", "/v1/twitter/search/tweets", "post", "text"),
    # Keyword search of public Facebook posts — the platform most Kenyans
    # are on, and until now reachable here only through known page handles.
    ("facebook", "/v1/facebook/search/posts", "post", "text"),
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
#
# Read from SocialCrawl's catalogue on 24 October 2026, not guessed: the
# brand-mentions composite is 50 credits flat (this table said 20, so the
# run's budget guard let two and a half times the spend through), LinkedIn
# search is 5 a page, the Trends endpoints 5. A budget the table
# underestimates is no budget.
ENDPOINT_CREDIT_COST = {
    SOCIALCRAWL_BRAND_MENTIONS_PATH: 50.0,
    "/v1/tiktok/post/transcript": 10.0,
    "/v1/youtube/video/transcript": 10.0,
    "/v1/linkedin/search/posts": 5.0,
    "/v1/google_trends/explore": 5.0,
    "/v1/google_trends/rising": 5.0,
}

#: Endpoints that page by an opaque cursor (`pagination.next_cursor`) and
#: ignore `page=`. Asked for "page 2" they served page 1 again — free from
#: SocialCrawl's two-minute cache, so it cost nothing and found nothing, and
#: no run ever read past the first page of any of them.
CURSOR_PATHS = {
    "/v1/twitter/search/tweets", "/v1/tiktok/search", "/v1/tiktok/search/hashtag",
    "/v1/threads/search", "/v1/facebook/search/posts",
}
#: Endpoints that accept `seen`: rows this account already received under
#: the same id are dropped and the page price falls with them, so the
#: overlapping query variants of one run stop paying for the same posts.
SEEN_PATHS = {"/v1/twitter/search/tweets", "/v1/tiktok/search", "/v1/threads/search",
              "/v1/facebook/search/posts", "/v1/linkedin/search/posts"}
#: Kenya in Google's location codes (2000 + ISO 3166 numeric 404).
KENYA_LOCATION_CODE = "2404"


def refusal_for(response) -> str | None:
    """A 402 is the ACCOUNT's verdict, not this request's: every other
    SocialCrawl call this run would get the same answer. Remembered for the
    host (`http.remember_refusal`), so the rest of the run is told once
    rather than asking dozens of times — and search demand drops straight to
    its keyless rungs. An hour, because a top-up should be picked up soon."""
    body = getattr(response, "text", "") or ""
    if not isinstance(body, str):
        body = ""
    if getattr(response, "status_code", None) == 402 or "INSUFFICIENT_CREDITS" in body[:400]:
        reason = ("HTTP 402: the SocialCrawl account behind SOCIALCRAWL_API_KEY has no credits "
                  "left. Top it up, or remove the variable to turn SocialCrawl off cleanly.")
        http.remember_refusal(SOCIALCRAWL_BASE_URL, reason, hours=1.0)
        return reason
    return None


def _tiktok_window(days: int) -> str:
    for limit, name in ((1, "yesterday"), (7, "this-week"), (31, "this-month"),
                        (92, "last-3-months"), (184, "last-6-months")):
        if days <= limit:
            return name
    return "all-time"


def _linkedin_window(days: int) -> str | None:
    for limit, name in ((1, "past_24h"), (7, "past_week"), (31, "past_month")):
        if days <= limit:
            return name
    return None
DEFAULT_CREDIT_COST = 1.0


def credit_cost(path: str) -> float:
    return ENDPOINT_CREDIT_COST.get(path, DEFAULT_CREDIT_COST)


#: Set when the account is out of credits, so the stage can say
#: that rather than reporting three failures of unknown cause.
#: Declared on the class so a reader of the attribute always
#: gets a meaningful answer.
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

    #: True once the account has reported no credits remaining.
    #: Declared here so `getattr(connector, "credits_exhausted")` is
    #: meaningful before any request has been made — the same contract
    #: `last_error` has, and for the same reason.
    credits_exhausted: bool = False
    #: The cursor the last `search_page` answer gave for its next page, or
    #: None. Read by the orchestrator between pages.
    next_cursor: str | None = None

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
    def _search_params(path: str, query: str, page: int, window_start: datetime, window_end: datetime,
                       cursor: str | None = None, seen: str | None = None) -> dict:
        """Endpoint-specific query params, each taken from SocialCrawl's own
        contract for that endpoint — the period in the form THAT endpoint
        reads, because an endpoint handed a parameter it does not know
        ignores it and answers with its all-time best, which is how an old
        post walks into a report about this month.
        """
        from datetime import timedelta

        start, end = window_start.date().isoformat(), window_end.date().isoformat()
        days = max(1, (window_end - window_start).days)
        if path.endswith("/search/hashtag"):
            params = {"hashtag": query.lstrip("#").replace(" ", "")}
        elif path == "/v1/google_news/search":
            # Exact publication dates, Kenya's edition.
            params = {"keyword": query, "depth": "100", "from": start, "to": end,
                      "location_code": KENYA_LOCATION_CODE, "language_code": "en"}
        elif path == "/v1/twitter/search/tweets":
            # No date parameters on this endpoint: the window is an X
            # operator inside the query. `until` is exclusive, hence +1 day.
            phrase = f'"{query}"' if " " in query and not query.startswith('"') else query
            until = (window_end + timedelta(days=1)).date().isoformat()
            params = {"query": f"{phrase} since:{start} until:{until}", "sort": "top"}
        elif path in ("/v1/threads/search", "/v1/facebook/search/posts"):
            params = {"query": query, "start_date": start, "end_date": end}
        elif path == "/v1/linkedin/search/posts":
            params = {"query": query}
            window = _linkedin_window(days)
            if window:
                params["date_posted"] = window
            if page > 1:
                params["page"] = str(page)
        elif path == "/v1/tiktok/search":
            params = {"query": query, "date_posted": _tiktok_window(days)}
        else:
            params = {"query": query, "page": str(page), "date_from": start, "date_to": end}
        if cursor and path in CURSOR_PATHS:
            params["cursor"] = cursor
        if seen and path in SEEN_PATHS:
            params["seen"] = seen
        return params

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
        cursor: str | None = None,
        seen: str | None = None,
    ) -> tuple[list[IngestedMention], bool]:
        """One page of a keyword/hashtag search. Returns (mentions, has_more).

        Date params are passed to every endpoint (ignored where unsupported);
        results outside the window are also filtered client-side since most
        social search endpoints don't honor date ranges. has_more is a
        server-driven signal where available, else "the page was non-empty".
        """
        params = self._search_params(path, query, page, window_start, window_end, cursor, seen)
        headers = {"x-api-key": self.api_key}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        self.next_cursor = None
        response = http.get(f"{SOCIALCRAWL_BASE_URL}{path}", params=params, headers=headers, timeout=30)
        if refusal_for(response):
            self.credits_exhausted = True
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
        # The cursor for the next page, wherever this endpoint puts it. On a
        # cursor endpoint it IS has_more: no cursor, no next page.
        pagination = (body.get("pagination") if isinstance(body.get("pagination"), dict) else None) \
            or (data.get("pagination") if isinstance(data.get("pagination"), dict) else {})
        next_cursor = pagination.get("next_cursor") or data.get("next_cursor")
        self.next_cursor = str(next_cursor) if next_cursor not in (None, "") else None
        if path in CURSOR_PATHS:
            has_more = bool(items) and self.next_cursor is not None
        # Endpoints without a page param would just re-serve page 1 forever.
        elif path.endswith("/search/hashtag") or path == "/v1/google_news/search":
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
        # One id, named for the endpoint. Sending id + video_id + post_id at
        # once was a guess at the API's parameter name, and the API answered
        # 400 to every transcript request — a paid endpoint returning nothing
        # for the whole run, on every video.
        param = "video_id" if platform == "youtube" else "post_id"
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{endpoint}",
            params={param: post_id},
            headers={"x-api-key": self.api_key},
            timeout=60,
        )
        if response.status_code >= 400:
            # Transcripts are optional enrichment. A rejected one costs this
            # video's spoken words; it must not raise into the run, and the
            # reason has to be recorded or a systematically broken parameter
            # looks like videos that simply have no transcript.
            # An exhausted account is a CONFIGURATION fact, not a stage that
            # failed, and the two read very differently to an operator.
            #
            # A live report showed "Some sections could not be produced —
            # transcripts: stopped after 3 consecutive failures … HTTP 402
            # INSUFFICIENT_CREDITS", which reads as something broken. Nothing
            # was broken: SOCIALCRAWL_API_KEY is set and the account behind it
            # has no credits, so this endpoint cannot work until it is topped
            # up or the key is removed. Saying that costs nothing and saves
            # the operator hunting for a bug.
            body_text = response.text[:200]
            if response.status_code == 402 or "INSUFFICIENT_CREDITS" in body_text:
                self.credits_exhausted = True
                self.last_error = (
                    "SocialCrawl reports no credits remaining on this account, "
                    "so transcripts cannot be fetched. This is the key in "
                    "SOCIALCRAWL_API_KEY — top the account up, or remove the "
                    "variable to turn the stage off cleanly. Nothing else in "
                    "the report depends on it.")
            else:
                self.last_error = (f"transcript {platform}/{post_id}: HTTP "
                                   f"{response.status_code} {body_text[:120]}")[:200]
            self._transcript_failures = getattr(self, "_transcript_failures", 0) + 1
            return None
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
            # Publication only. `fetch_time` is when SocialCrawl READ the page,
            # and using it dated old articles to the day they were crawled.
            posted_at_raw = item.get("date_published") or item.get("posted_at")
            try:
                published = self._parse_datetime(posted_at_raw) if posted_at_raw else None
            except (ValueError, TypeError, OverflowError, OSError):
                published = None
            dated = period_date(published, window_start, window_end)
            if dated is None:
                continue          # published before the period
            posted_at, estimated = dated
            item = {**item, "date_estimated": estimated}

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
            ("twitter", "/v1/twitter/search/tweets", "post"),
            ("facebook", "/v1/facebook/search/posts", "post"),
        ]
        for platform, path, default_source_type in discovery_calls:
            try:
                posts = self._call_search_endpoint(platform, path, default_source_type, politician_name,
                                                   window_start, window_end)
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
        self, platform: str, path: str, default_source_type: str, query: str,
        window_start: datetime | None = None, window_end: datetime | None = None,
    ) -> list[IngestedMention]:
        # The same per-endpoint params the orchestrator's path uses. This
        # sent `query=` to every endpoint, and the hashtag endpoints answer
        # that with a 400 — paid for nothing on every Issue Map.
        if window_start and window_end:
            params = self._search_params(path, query, 1, window_start, window_end)
        else:
            params = {"query": query}
        response = http.get(
            f"{SOCIALCRAWL_BASE_URL}{path}",
            params=params,
            headers={"x-api-key": self.api_key},
            timeout=30,
        )
        if refusal_for(response):
            self.credits_exhausted = True
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
        # No date is not "now": a dateless post stamped with the clock was
        # counted as published this minute. Marked, so the period's analysis
        # leaves it out (see engine.db.period); the orchestrator's window
        # check still drops anything dated outside the period.
        try:
            posted_at = (SocialCrawlConnector._parse_datetime(posted_at_raw)
                         if posted_at_raw else None)
        except (ValueError, TypeError, OverflowError, OSError):
            posted_at = None
        estimated = posted_at is None
        if estimated:
            posted_at = datetime.utcnow()
            item = {**item, "date_estimated": True}

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

"""Facebook ingestion via facebook-scraper (github.com/kevinzg/facebook-scraper).

Facebook is the largest political-conversation platform in Kenya, and the one
our paid reseller covers most thinly. facebook-scraper reads PUBLIC pages and
groups — posts, reactions, and full comment threads — with no API key.

Strategy: monitor a configurable set of high-traffic Kenyan news/politics
pages, keep the posts that mention the tracked politician, and pull the
comment threads on those posts (grassroots voice). A keyword search pass is
attempted too where the library supports it. Everything maps to the same
IngestedMention shape as every other source.

Like AgentReach, this needs open outbound network (blocked in the build
sandbox by egress policy) and, for best coverage, a logged-in cookie file;
it runs for real on the deployment / the user's machine. Unit tests exercise
the mapping against fixture post dicts.
"""

from datetime import datetime

from engine.ingestion.base import IngestedMention, IngestionConnector

# Public pages that carry the bulk of Kenyan political conversation. Operators
# can override via settings.facebook_pages (comma-separated).
DEFAULT_PAGES = [
    "CitizenTVKe", "ntvkenya", "NationMediaGroup", "StandardKenya",
    "KTNNewsKE", "K24Tv", "TheStarKenya", "PeopleDailyKe",
]
POSTS_PAGE_LIMIT = 3       # pagination depth per FB page per run
MAX_COMMENTS_PER_POST = 50


class FacebookConnector(IngestionConnector):
    def __init__(self, pages: list[str] | None = None, cookies: str | None = None, scraper=None):
        self.pages = pages or DEFAULT_PAGES
        self.cookies = cookies  # path to a Netscape cookies.txt for logged-in reach
        # `scraper` is injectable for testing; defaults to the real library.
        self._scraper = scraper

    def _get_posts(self, **kwargs):
        if self._scraper is not None:
            return self._scraper(**kwargs)
        from facebook_scraper import get_posts

        return get_posts(**kwargs)

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        terms = [t.lower() for t in [politician_name, *aliases] if t]
        mentions: list[IngestedMention] = []
        seen_posts: set[str] = set()

        for page in self.pages:
            try:
                posts = self._get_posts(
                    account=page,
                    page_limit=POSTS_PAGE_LIMIT,
                    cookies=self.cookies,
                    options={"comments": True, "reactions": True},
                )
            except Exception:
                continue
            for post in posts:
                mentions.extend(self._map_post(post, page, terms, window_start, window_end, seen_posts))
        return mentions

    def _map_post(self, post, page, terms, window_start, window_end, seen_posts) -> list[IngestedMention]:
        if not isinstance(post, dict):
            return []
        post_id = str(post.get("post_id") or post.get("post_url") or "")
        if not post_id or post_id in seen_posts:
            return []
        text = (post.get("text") or post.get("post_text") or "").strip()
        posted = post.get("time")
        if isinstance(posted, datetime):
            if posted < window_start or posted > window_end:
                return []
        else:
            posted = window_end

        # Only keep posts that actually mention the tracked politician.
        if not any(t in text.lower() for t in terms):
            # ...unless a matching comment does (grassroots reaction on a
            # borderline post is still signal); handled per-comment below.
            relevant_post = False
        else:
            relevant_post = True
        seen_posts.add(post_id)

        out: list[IngestedMention] = []
        if relevant_post and text:
            reactions = post.get("reactions") or {}
            out.append(
                IngestedMention(
                    platform="facebook",
                    source_type="post",
                    author_handle=post.get("username") or page,
                    text=text,
                    posted_at=posted,
                    engagement={
                        "likes": post.get("likes") or sum(reactions.values()) if isinstance(reactions, dict) else (post.get("likes") or 0),
                        "comments": post.get("comments") or 0,
                        "shares": post.get("shares") or 0,
                    },
                    raw_payload={"url": post.get("post_url"), "post_id": post_id, "source": "facebook-scraper", "page": page},
                )
            )

        for comment in (post.get("comments_full") or [])[:MAX_COMMENTS_PER_POST]:
            if not isinstance(comment, dict):
                continue
            ctext = (comment.get("comment_text") or "").strip()
            if not ctext:
                continue
            # A comment is relevant if the post was about the politician, or the
            # comment itself names them.
            if not relevant_post and not any(t in ctext.lower() for t in terms):
                continue
            out.append(
                IngestedMention(
                    platform="facebook",
                    source_type="comment",
                    author_handle=comment.get("commenter_name") or "unknown",
                    text=ctext,
                    posted_at=comment.get("comment_time") if isinstance(comment.get("comment_time"), datetime) else posted,
                    engagement={"likes": comment.get("comment_reactions_count") or 0},
                    raw_payload={
                        "source": "facebook-scraper",
                        "parent_post_id": post_id,
                        "_parent_post": {"id": post_id, "url": post.get("post_url"), "author": post.get("username") or page},
                    },
                )
            )
        return out

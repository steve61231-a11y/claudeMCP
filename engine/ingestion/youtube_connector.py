"""YouTube ingestion via yt-dlp — free, keyless.

Kenyan political discourse lives heavily on YouTube: news channels (NTV, Citizen,
KTN), commentary and interviews. yt-dlp searches and extracts metadata with no
API key. We pull the top matching videos (title + description as the mention
text) and, for the highest-view hits, a bounded slice of top comments — the
grassroots voice.

Metadata-only extraction (`extract_flat` for search, per-video info without
download) keeps it fast and avoids downloading any media. Best-effort: any
failure degrades to an empty list.
"""

import threading
import time
from datetime import datetime
from urllib.parse import quote_plus

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector, period_date

MAX_VIDEOS = 20
MAX_COMMENTS_PER_VIDEO = 20
COMMENT_VIDEOS = 5  # only fetch comments for the top-N videos (comments are slow)

#: YouTube's own "upload date" search filters. The plain search ranks by
#: relevance across all time: measured on 21 Oct 2026, 14 of 40 results for
#: a one-month query predated the month, the oldest from 2018 — and with no
#: date on a flat result, every one was counted as published this month.
#: Filtered to the month, 895 of 897 carried a date and 19 sat a few days
#: outside it, which the period rule then removes exactly.
UPLOADED_THIS_MONTH = "EgIIBA%253D%253D"
UPLOADED_THIS_YEAR = "EgIIBQ%253D%253D"

#: A filtered results page paginates for as long as it is asked to — one
#: query returned ~450 entries. Read a little more than is kept, so the
#: period rule has room to drop the edges.
SEARCH_PAGE = 30

#: Comments need a full extraction per video, and YouTube answers that from
#: datacenter addresses with "Sign in to confirm you're not a bot". Once it
#: has said so, asking again for every video of every query only costs time.
_COMMENTS_REFUSED_UNTIL = 0.0
_COMMENTS_LOCK = threading.Lock()
_COMMENTS_REFUSAL_SECONDS = 6 * 3600


def _max_videos() -> int:
    return 6 if settings.low_memory else MAX_VIDEOS


def _comment_videos() -> int:
    # Comment extraction is the slowest/heaviest yt-dlp path; skip it entirely
    # on a memory-constrained instance (video titles/descriptions still ingest).
    return 0 if settings.low_memory else COMMENT_VIDEOS


class YouTubeConnector(IngestionConnector):
    def __init__(self, ydl_factory=None):
        # Injectable for tests; real one builds a yt_dlp.YoutubeDL.
        self._ydl_factory = ydl_factory
        self._comment_failures = 0
        self.last_error: str | None = None

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime,
        window_end: datetime, query: str | None = None
    ) -> list[IngestedMention]:
        """Search YouTube for one query.

        `query` is the one this task was planned to run — see
        `orchestrator.KEYWORD_CONNECTORS`. Without it every task for this
        source re-derived the same bare-name phrase, so a twelve-variant
        expansion made twelve identical requests.
        """
        days = max(1, -(-int((window_end - window_start).total_seconds()) // 86400))
        entries = self._search(query or politician_name, days)
        if not entries:
            return []

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        kept = 0
        for entry in entries:
            if kept >= _max_videos():
                break
            vid = str(entry.get("id") or "")
            title = (entry.get("title") or "").strip()
            if not vid or not title or vid in seen:
                continue
            seen.add(vid)
            desc = (entry.get("description") or "").strip()
            text = f"{title}\n\n{desc}".strip() if desc else title
            # An unparseable date is NOT a date. Falling back to the
            # window end keeps ordering and windowing working, and it
            # also stamps every undated item onto one day — so the
            # volume chart drew a spike that never happened and the
            # peak-day figure counted it. Say which it is; the
            # consumer decides what to do with an unknown.
            dated = period_date(self._published(entry), window_start, window_end)
            if dated is None:
                continue          # uploaded before the period
            posted, estimated = dated
            channel = entry.get("channel") or entry.get("uploader") or "youtube"
            mentions.append(
                IngestedMention(
                    platform="youtube",
                    source_type="video",
                    author_handle=channel,
                    text=text[:4000],
                    posted_at=posted,
                    engagement={
                        "views": int(entry.get("view_count") or 0),
                        "likes": int(entry.get("like_count") or 0),
                        "comments": int(entry.get("comment_count") or 0),
                    },
                    raw_payload={
                        "date_estimated": estimated,
                        "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                        "video_id": vid,
                        "channel": channel,
                        "source": "youtube",
                    },
                )
            )
            kept += 1
            # Grassroots comments for the top few videos only.
            if kept <= _comment_videos():
                for c in self._comments(vid)[:MAX_COMMENTS_PER_VIDEO]:
                    ctext = (c.get("text") or "").strip()
                    if not ctext:
                        continue
                    # A reaction is dated by ITSELF, never by the video it
                    # sits under. Comments used to inherit their video's
                    # date, so a fresh reply to a 2022 election clip and a
                    # 2022 reply alike were filed under this month. A
                    # reaction counts only when it falls in the period.
                    cdated = period_date(self._comment_time(c), window_start, window_end)
                    if cdated is None:
                        continue
                    cposted, cestimated = cdated
                    mentions.append(
                        IngestedMention(
                            platform="youtube",
                            source_type="comment",
                            author_handle=str(c.get("author") or "viewer"),
                            text=ctext[:2000],
                            posted_at=cposted,
                            engagement={"likes": int(c.get("like_count") or 0)},
                            raw_payload={
                                "date_estimated": cestimated,
                                "url": entry.get("webpage_url"),
                                "video_id": vid,
                                "_parent_post": vid,
                                "source": "youtube",
                            },
                        )
                    )
        return mentions

    # --- yt-dlp seams (overridable in tests) ---------------------------------

    def _search(self, query: str, days: int = 30) -> list[dict]:
        try:
            ydl = self._build_ydl({
                "extract_flat": True, "skip_download": True,
                "playlistend": max(SEARCH_PAGE, _max_videos()),
                # Without this a flat result carries no date at all; with it,
                # yt-dlp reads YouTube's "3 days ago" into a timestamp.
                "extractor_args": {"youtubetab": {"approximate_date": [""]}},
            })
            if ydl is None:
                return []
            upload = UPLOADED_THIS_MONTH if days <= 31 else UPLOADED_THIS_YEAR
            target = ("https://www.youtube.com/results?search_query="
                      + quote_plus(f"{query} Kenya") + "&sp=" + upload)
            with ydl as y:
                info = y.extract_info(target, download=False)
            return info.get("entries", []) if info else []
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return []

    def _comments(self, video_id: str) -> list[dict]:
        global _COMMENTS_REFUSED_UNTIL
        with _COMMENTS_LOCK:
            if time.monotonic() < _COMMENTS_REFUSED_UNTIL:
                return []
        try:
            ydl = self._build_ydl(
                {"getcomments": True, "skip_download": True,
                 "extractor_args": {"youtube": {"max_comments": [str(MAX_COMMENTS_PER_VIDEO)]}}}
            )
            if ydl is None:
                return []
            with ydl as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return (info or {}).get("comments", []) or []
        except Exception as exc:  # noqa: BLE001
            # Comments are the grassroots voice the public-voice analyst leans
            # on hardest. Losing them all silently reads as a subject nobody
            # commented on.
            self._comment_failures += 1
            self.last_error = (f"comments unavailable ({self._comment_failures} video(s)): "
                               f"{type(exc).__name__}: {exc}")[:200]
            if "not a bot" in str(exc) or "Sign in" in str(exc):
                with _COMMENTS_LOCK:
                    _COMMENTS_REFUSED_UNTIL = time.monotonic() + _COMMENTS_REFUSAL_SECONDS
            return []

    def _build_ydl(self, opts: dict):
        if self._ydl_factory is not None:
            return self._ydl_factory(opts)
        try:
            import yt_dlp
        except Exception:
            return None
        opts = {"quiet": True, "no_warnings": True, **opts}
        return yt_dlp.YoutubeDL(opts)

    @classmethod
    def _published(cls, entry: dict) -> datetime | None:
        """A result's upload time: the approximate timestamp a flat search
        carries when asked for it, else an exact upload date, else None."""
        ts = entry.get("timestamp") or entry.get("release_timestamp")
        if ts:
            try:
                return datetime.utcfromtimestamp(int(ts))
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        return cls._parse_upload(entry.get("upload_date"))

    @staticmethod
    def _comment_time(comment: dict) -> datetime | None:
        ts = comment.get("timestamp")
        if not ts:
            return None
        try:
            return datetime.utcfromtimestamp(int(ts))
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _parse_upload(value) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y%m%d")
        except ValueError:
            return None

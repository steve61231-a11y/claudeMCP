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

from datetime import datetime

from engine.config import settings
from engine.ingestion.base import IngestedMention, IngestionConnector

MAX_VIDEOS = 20
MAX_COMMENTS_PER_VIDEO = 20
COMMENT_VIDEOS = 5  # only fetch comments for the top-N videos (comments are slow)


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

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        entries = self._search(politician_name)
        if not entries:
            return []

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for i, entry in enumerate(entries[:_max_videos()]):
            vid = str(entry.get("id") or "")
            title = (entry.get("title") or "").strip()
            if not vid or not title or vid in seen:
                continue
            seen.add(vid)
            desc = (entry.get("description") or "").strip()
            text = f"{title}\n\n{desc}".strip() if desc else title
            posted = self._parse_upload(entry.get("upload_date")) or window_end
            posted = min(max(posted, window_start), window_end)
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
                        "url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
                        "video_id": vid,
                        "channel": channel,
                        "source": "youtube",
                    },
                )
            )
            # Grassroots comments for the top few videos only.
            if i < _comment_videos():
                for c in self._comments(vid)[:MAX_COMMENTS_PER_VIDEO]:
                    ctext = (c.get("text") or "").strip()
                    if not ctext:
                        continue
                    mentions.append(
                        IngestedMention(
                            platform="youtube",
                            source_type="comment",
                            author_handle=str(c.get("author") or "viewer"),
                            text=ctext[:2000],
                            posted_at=posted,
                            engagement={"likes": int(c.get("like_count") or 0)},
                            raw_payload={
                                "url": entry.get("webpage_url"),
                                "video_id": vid,
                                "_parent_post": vid,
                                "source": "youtube",
                            },
                        )
                    )
        return mentions

    # --- yt-dlp seams (overridable in tests) ---------------------------------

    def _search(self, query: str) -> list[dict]:
        try:
            ydl = self._build_ydl({"extract_flat": True, "skip_download": True})
            if ydl is None:
                return []
            with ydl as y:
                info = y.extract_info(f"ytsearch{_max_videos()}:{query} Kenya", download=False)
            return info.get("entries", []) if info else []
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return []

    def _comments(self, video_id: str) -> list[dict]:
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
        except Exception:
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

    @staticmethod
    def _parse_upload(value) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.strptime(str(value), "%Y%m%d")
        except ValueError:
            return None

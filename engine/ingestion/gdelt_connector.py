"""GDELT ingestion — the historical-news workhorse.

GDELT (gdeltproject.org) is a free, no-key global news database that indexes
worldwide coverage in near-real-time and keeps years of history. Its DOC 2.0
API supports full-text search with a date range, which is exactly what our
other sources lack: SocialCrawl is point-in-time and NewsAPI's free tier only
reaches ~30 days back. GDELT closes the historical gap — a 6-month or 2-year
window returns real articles from the whole period.

Maps each article to an IngestedMention (platform = source domain,
source_type = "article"). Body text is not returned by GDELT (only title +
metadata); the WaybackConnector can recover full text for dead links.
"""

from datetime import datetime

from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 250  # GDELT DOC API hard cap per call


class GdeltConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        # Quote the primary name; OR in aliases so "CS Mbadi" etc. all match.
        terms = [politician_name, *[a for a in aliases if a]]
        query = " OR ".join(f'"{t}"' for t in terms[:5])
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(MAX_RECORDS),
            "sort": "datedesc",
            "startdatetime": window_start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": window_end.strftime("%Y%m%d%H%M%S"),
        }
        try:
            response = http.get(GDELT_DOC_URL, params=params, timeout=30)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            return []

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for art in body.get("articles") or []:
            url = art.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            posted = self._parse_seendate(art.get("seendate")) or window_end
            if not (window_start <= posted <= window_end):
                # GDELT occasionally returns slightly out-of-window items; keep
                # them anyway (still historical signal) but clamp the date.
                posted = min(max(posted, window_start), window_end)
            title = (art.get("title") or "").strip()
            if not title:
                continue
            mentions.append(
                IngestedMention(
                    platform=art.get("domain") or "news",
                    source_type="article",
                    author_handle=art.get("domain") or "news",
                    text=title,
                    posted_at=posted,
                    engagement={},
                    raw_payload={
                        "url": url,
                        "title": title,
                        "language": art.get("language"),
                        "sourcecountry": art.get("sourcecountry"),
                        "source": "gdelt",
                    },
                )
            )
        return mentions

    @staticmethod
    def _parse_seendate(value) -> datetime | None:
        if not value:
            return None
        # GDELT seendate: "20260615T103000Z" or "20260615103000".
        cleaned = str(value).replace("T", "").replace("Z", "")
        for fmt in ("%Y%m%d%H%M%S", "%Y%m%d"):
            try:
                return datetime.strptime(cleaned[: len(fmt.replace("%", "")) + 2], fmt)
            except ValueError:
                continue
        try:
            return datetime.strptime(cleaned[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return None

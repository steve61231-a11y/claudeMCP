"""Google News RSS ingestion — the highest-value FREE, keyless news source for
Kenyan politics.

GDELT indexes mostly large English outlets and is thin on Kenyan local figures.
Google News RSS aggregates the outlets that actually cover Kenyan politics daily
— Nation, Standard, Citizen, The Star, Capital FM, Tuko — with no API key and no
credits. The `gl=KE&hl=en-KE&ceid=KE:en` locale biases results to Kenya.

Endpoint: https://news.google.com/rss/search?q=<terms>&hl=en-KE&gl=KE&ceid=KE:en
Returns an RSS feed; we parse it with the stdlib (no feedparser dependency) and
map each item to an IngestedMention(platform=source domain, source_type="article").
Article bodies are then filled in by the existing trafilatura enrichment step in
the orchestrator, so the corpus holds full journalism, not just headlines.
"""

from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector

RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-KE&gl=KE&ceid=KE:en"
MAX_ITEMS = 80


class GoogleNewsRssConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        # One OR-query keeps it to a single request; quote the primary name so
        # "John Mbadi" matches as a phrase, OR in the strongest aliases.
        terms = [politician_name, *[a for a in aliases if a]][:4]
        query = " OR ".join(f'"{t}"' for t in terms)
        url = RSS_URL.format(query=quote_plus(query))
        try:
            resp = http.get(url, timeout=25)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:
            return []

        mentions: list[IngestedMention] = []
        seen: set[str] = set()
        for item in root.iter("item"):
            if len(mentions) >= MAX_ITEMS:
                break
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            posted = self._parse_date(item.findtext("pubDate")) or window_end
            posted = min(max(posted, window_start), window_end)
            source_el = item.find("source")
            outlet = (source_el.text if source_el is not None else None) or "google_news"
            mentions.append(
                IngestedMention(
                    platform=outlet.lower().replace(" ", "")[:40],
                    source_type="article",
                    author_handle=outlet,
                    text=title,
                    posted_at=posted,
                    engagement={},
                    raw_payload={
                        "url": link,
                        "title": title,
                        "outlet": outlet,
                        "source": "google_news_rss",
                    },
                )
            )
        return mentions

    @staticmethod
    def _parse_date(value) -> datetime | None:
        if not value:
            return None
        try:
            dt = parsedate_to_datetime(value)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except (TypeError, ValueError):
            return None

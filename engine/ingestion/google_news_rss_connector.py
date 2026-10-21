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
from urllib.parse import quote_plus, urlsplit
from xml.etree import ElementTree as ET

from engine.ingestion import http
from engine.ingestion.base import IngestedMention, IngestionConnector, period_date

RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-KE&gl=KE&ceid=KE:en"
MAX_ITEMS = 80


class GoogleNewsRssConnector(IngestionConnector):
    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime,
        window_end: datetime, query: str | None = None
    ) -> list[IngestedMention]:
        """One Google News RSS request.

        `query` is the one this task was planned to run — see
        `orchestrator.KEYWORD_CONNECTORS`. Without it every task for this
        source re-derived the same bare-name phrase, so a twelve-variant
        expansion made twelve identical requests.
        """
        if not query:
            # One OR-query keeps it to a single request; quote the primary name
            # so "John Mbadi" matches as a phrase, OR in the strongest aliases.
            terms = [politician_name, *[a for a in aliases if a]][:4]
            query = " OR ".join(f'"{t}"' for t in terms)
        # Restrict to the period AT THE SOURCE. Without this Google News
        # returns its best matches from any year: measured on 21 Oct 2026,
        # 382 of 806 results for a one-month query predated the month, and
        # every one was clamped into it. `when:Nd` keeps them out (0 of 699)
        # at almost no cost in volume.
        days = max(1, -(-int((window_end - window_start).total_seconds()) // 86400))
        if "when:" not in query:
            query = f"{query} when:{days}d"
        url = RSS_URL.format(query=quote_plus(query))
        try:
            resp = http.get(url, timeout=25)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
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
            # An unparseable date is NOT a date. Falling back to the
            # window end keeps ordering and windowing working, and it
            # also stamps every undated item onto one day — so the
            # volume chart drew a spike that never happened and the
            # peak-day figure counted it. Say which it is; the
            # consumer decides what to do with an unknown.
            dated = period_date(self._parse_date(item.findtext("pubDate")),
                                window_start, window_end)
            if dated is None:
                continue          # published before the period
            posted, estimated = dated
            source_el = item.find("source")
            outlet = (source_el.text if source_el is not None else None) or "google_news"
            # The outlet's own site, from <source url=…>. The item link is a
            # news.google.com redirect, and the outlet NAME squashed into a
            # key ("thestar", "nationafrica") matched no Kenyan domain — so
            # the Daily Nation was counted as international coverage.
            outlet_domain = ((urlsplit(source_el.get("url") or "").hostname or "")
                             if source_el is not None else "").lower().removeprefix("www.")
            mentions.append(
                IngestedMention(
                    platform=outlet.lower().replace(" ", "")[:40],
                    source_type="article",
                    author_handle=outlet,
                    text=title,
                    posted_at=posted,
                    engagement={},
                    raw_payload={
                        "date_estimated": estimated,
                        "url": link,
                        "title": title,
                        "outlet": outlet,
                        "outlet_domain": outlet_domain or None,
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

import json
from datetime import datetime
from pathlib import Path

from engine.ingestion.base import IngestedMention, IngestionConnector

CURATED_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data" / "curated"


class CuratedResearchConnector(IngestionConnector):
    """Real-world mentions hand-curated from Kenyan public sources.

    Direct X/Facebook/LinkedIn scraping needs either paid platform APIs or a
    third-party connector (SocialCrawl, Bright Data) we don't have credentials
    for, and this sandbox's network egress only allowlists a handful of hosts
    — it can't reach Kenyan news sites or NewsAPI directly. This connector is
    the stopgap: a researcher (or an agent with web-search access) fetches
    real public reporting/statements and drops them in
    `mock-data/curated/<politician_slug>.json` in the same shape NewsApiConnector
    would produce, with the source URL kept in raw_payload for provenance.
    """

    def fetch(
        self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime
    ) -> list[IngestedMention]:
        slug = politician_name.lower().replace(" ", "_")
        fixture_path = CURATED_DIR / f"{slug}.json"
        if not fixture_path.exists():
            return []

        mentions: list[IngestedMention] = []
        for item in json.loads(fixture_path.read_text()):
            posted_at = datetime.fromisoformat(item["posted_at"])
            if not (window_start <= posted_at <= window_end):
                continue
            mentions.append(
                IngestedMention(
                    platform=item.get("platform", "news"),
                    source_type=item.get("source_type", "article"),
                    author_handle=item["author_handle"],
                    text=item["text"],
                    posted_at=posted_at,
                    engagement=item.get("engagement", {}),
                    raw_payload=item,
                )
            )
        return mentions

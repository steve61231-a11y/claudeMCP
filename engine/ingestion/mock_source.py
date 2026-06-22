import json
from datetime import datetime
from pathlib import Path

from engine.ingestion.base import IngestedMention, IngestionConnector

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data" / "fixtures"

PLATFORMS = ["x", "facebook", "linkedin", "tiktok"]


class MockIngestionConnector(IngestionConnector):
    """Reads pre-generated fixture JSON files shaped like real connector output.

    This is the seam future connectors (SocialCrawl MCP, Bright Data Social MCP)
    plug into in place of fixture files.
    """

    def fetch(self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime) -> list[IngestedMention]:
        mentions: list[IngestedMention] = []
        for platform in PLATFORMS:
            fixture_path = FIXTURES_DIR / f"{platform}.json"
            if not fixture_path.exists():
                continue
            raw_items = json.loads(fixture_path.read_text())
            for item in raw_items:
                posted_at = datetime.fromisoformat(item["posted_at"])
                if not (window_start <= posted_at <= window_end):
                    continue
                mentions.append(
                    IngestedMention(
                        platform=platform,
                        source_type=item.get("source_type", "post"),
                        author_handle=item["author_handle"],
                        text=item["text"],
                        posted_at=posted_at,
                        engagement=item.get("engagement", {}),
                        raw_payload=item,
                    )
                )
        return mentions

from pathlib import Path

from engine.config import settings
from engine.ingestion.base import IngestionConnector
from engine.ingestion.mock_source import MockIngestionConnector

CURATED_DIR = Path(__file__).resolve().parent.parent.parent / "mock-data" / "curated"


def get_ingestion_connector(politician_name: str | None = None) -> IngestionConnector:
    """Picks a connector without pipeline.py knowing which one it got:

    1. Curated real-world fixture for this politician, if one exists
       (`mock-data/curated/<slug>.json`) — used when the engine's own network
       can't reach news/social APIs directly (e.g. this sandbox's egress
       allowlist), but real content has been gathered out-of-band.
    2. NewsApiConnector, if NEWSAPI_KEY is configured and newsapi.org is
       reachable from this deployment.
    3. Mock fixtures otherwise.
    """
    if politician_name:
        slug = politician_name.lower().replace(" ", "_")
        if (CURATED_DIR / f"{slug}.json").exists():
            from engine.ingestion.curated_source import CuratedResearchConnector

            return CuratedResearchConnector()

    if settings.newsapi_key:
        from engine.ingestion.news_connector import NewsApiConnector

        return NewsApiConnector()

    return MockIngestionConnector()

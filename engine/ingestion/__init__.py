from engine.config import settings
from engine.ingestion.base import IngestionConnector
from engine.ingestion.mock_source import MockIngestionConnector


def get_ingestion_connector() -> IngestionConnector:
    """Real connector when NEWSAPI_KEY is configured, mock fixtures otherwise.

    Keeps the swap a config change, not a code change — pipeline.py only
    ever depends on the IngestionConnector interface.
    """
    if settings.newsapi_key:
        from engine.ingestion.news_connector import NewsApiConnector

        return NewsApiConnector()
    return MockIngestionConnector()

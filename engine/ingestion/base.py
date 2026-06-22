from abc import ABC, abstractmethod
from datetime import datetime
from typing import TypedDict


class IngestedMention(TypedDict):
    platform: str
    source_type: str  # post | comment
    author_handle: str
    text: str
    posted_at: datetime
    engagement: dict
    raw_payload: dict


class IngestionConnector(ABC):
    """Contract every ingestion source (mock or real) must satisfy.

    Real connectors (SocialCrawl MCP, Bright Data Social MCP, etc.) implement
    this same interface so the pipeline never has to change when ingestion
    sources are swapped.
    """

    @abstractmethod
    def fetch(self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime) -> list[IngestedMention]:
        ...

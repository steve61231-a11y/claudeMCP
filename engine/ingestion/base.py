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

    `last_error` is part of that contract. A connector that returns [] because
    the host refused it and one that returns [] because the subject genuinely
    has no coverage are the same value to every caller — and that is how a
    search for a real politician can come back with two mentions and look like
    an honest answer. A connector that fails MUST say so here.
    """

    #: Why the last fetch returned less than it should have, or None. Declared
    #: on the class so `getattr(connector, "last_error")` is always meaningful,
    #: even for a connector that never sets it.
    last_error: str | None = None

    @abstractmethod
    def fetch(self, politician_name: str, aliases: list[str], window_start: datetime, window_end: datetime) -> list[IngestedMention]:
        ...

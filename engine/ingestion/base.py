from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TypedDict


def period_date(published: datetime | None, window_start: datetime,
                window_end: datetime) -> tuple[datetime, bool] | None:
    """When an item counts as published, judged against the reporting period.

    Returns `(posted_at, date_estimated)`, or None when the item was published
    BEFORE the period and so is not this report's evidence at all.

    Every collector used to clamp a real date into the window —
    `min(max(posted, window_start), window_end)` — so a video uploaded for
    the 2022 election, returned by a relevance-ranked search, was stored as
    published on the first day of this month. An investor found "mentions
    going back to the last election" in a one-month report. Measured from
    GitHub's network on 21 Oct 2026: 47% of Google News results and 35% of
    YouTube results for a one-month query predated the month, every one of
    them dated into it. A date is a fact about the item; it is never moved.

    An item with no date at all is kept and marked: `date_estimated` items
    are excluded from the period's analysis by the pipeline, because "we
    could not date this" must never read as "this happened this month".
    """
    if published is None:
        return window_end, True
    if published.tzinfo is not None:
        published = published.astimezone(timezone.utc).replace(tzinfo=None)
    if published < window_start:
        return None
    return published, False


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

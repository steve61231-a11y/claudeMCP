from datetime import datetime

from pydantic import BaseModel


class PoliticianCreate(BaseModel):
    name: str
    aliases: list[str] = []
    keywords: list[str] = []
    titles: list[str] = []
    swahili_terms: list[str] = []
    tracked_hashtags: list[str] = []
    social_handles: dict[str, str] = {}


class PoliticianOut(BaseModel):
    id: str
    name: str
    aliases: list[str]
    keywords: list[str]
    titles: list[str] | None = None
    swahili_terms: list[str] | None = None
    tracked_hashtags: list[str] | None = None
    social_handles: dict[str, str] | None = None

    class Config:
        from_attributes = True


class RunRequest(BaseModel):
    period: str = "weekly"  # daily|weekly|monthly
    window_start: datetime
    window_end: datetime
    # SocialCrawl credit guardrail for this run; 0 = unlimited (depth-first).
    credit_budget: float = 0.0


class RunResult(BaseModel):
    report_id: str
    payload: dict

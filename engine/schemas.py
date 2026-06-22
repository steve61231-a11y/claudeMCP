from datetime import datetime

from pydantic import BaseModel


class PoliticianCreate(BaseModel):
    name: str
    aliases: list[str] = []
    keywords: list[str] = []


class PoliticianOut(BaseModel):
    id: str
    name: str
    aliases: list[str]
    keywords: list[str]

    class Config:
        from_attributes = True


class RunRequest(BaseModel):
    period: str = "weekly"  # daily|weekly|monthly
    window_start: datetime
    window_end: datetime


class RunResult(BaseModel):
    report_id: str
    payload: dict

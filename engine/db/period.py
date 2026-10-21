"""Which stored mentions belong to a reporting period.

A mention belongs to a period when it was PUBLISHED in it — not when it was
collected, not when something replied to it, and not on a date the engine had
to guess. Items whose date could not be established are stored with
`raw_payload["date_estimated"] = true` and a stand-in `posted_at` (the end of
the window they were collected in), which keeps ordering working and puts
every one of them on the busiest day of every chart. They are left out here,
once, so no query that windows by `posted_at` can count them.

The investor's rule, as he put it: "focus on the date of publication first,
and then reactions only when they fall in the reporting period."
"""

from __future__ import annotations

from sqlalchemy import func

from engine.db.models import RawMention


def dated():
    """SQL: this mention's publication date is known."""
    return func.coalesce(RawMention.raw_payload["date_estimated"].astext, "false") != "true"


def published_between(window_start, window_end) -> tuple:
    """SQL filters for mentions published inside [window_start, window_end]."""
    return (
        RawMention.posted_at >= window_start,
        RawMention.posted_at <= window_end,
        dated(),
    )

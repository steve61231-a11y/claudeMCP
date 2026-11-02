"""Statutory deadlines ahead, from the Kenya Gazette's own words — no model.

Phase 4 of docs/ISSUE_MAP_FOUNDATION.md: "from the Gazette, statutory
deadlines". What the Gazette can be read for was OBSERVED from GitHub's
network (`scripts/gazette_probe.py`, three rounds, 2 Nov 2026):

  * Kenya Law's search API restricts to Gazette issues with
    `nature=Gazette`, orders newest first with `ordering=-date`, and holds
    to a date with `date__gte=`;
  * a card is a whole Gazette ISSUE, and what it shows of the issue is the
    passages around the search terms ("Page 4 : [...] … [...]");
  * the issue itself — on new.kenyalaw.org and on gazettes.africa, the same
    platform — is refused to a server (HTTP 202 with no body, HTTP 403).

So the deadline is read from the passages, and from nothing else. A
deadline counts only where ONE passage names the issue and states the
deadline:

  * a calendar date after today beside deadline words ("submit memoranda
    on or before Friday, 13th November, 2026") — stated in the source; or
  * "within thirty (30) days from the date of this notice / of publication
    / hereof" — the day is the issue's date plus those days, COUNTED, and
    the entry says it was counted and from what. Any other anchor ("of
    service", "of the decision") is not read: its start is not printed.

The full notice is not readable, so a deadline not shown in a passage is
not seen — the map's report says so, and "no deadline found" is never "no
deadline".
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from engine.evidence import upcoming

#: Words that make a dated passage a deadline rather than a mention.
STATED = re.compile(r"\b(?:on or before|not later than|no later than|by close of business on|closing date|"
                    r"deadline|submissions?\s+(?:shall|must|should)\s+(?:be\s+)?(?:made|received|submitted))\b",
                    re.I)
#: "within thirty (30) days from the date hereof", "within 14 days of the
#: publication of this notice". The anchor must be the notice itself.
RELATIVE = re.compile(
    r"\bwithin\s+(?:[a-z\-]+\s*)?\(?\s*(\d{1,3})\s*\)?\s*,?\s*days\s+(?:from|of|after)\s+"
    r"(?:the\s+)?(?:date\s+(?:of\s+)?)?(?:this\s+notice|hereof|(?:the\s+)?publication(?:\s+of\s+this\s+notice)?)\b",
    re.I)
_PAGE = re.compile(r"\bPage\s+\d+\s*:\s*", re.I)
_SPLIT = re.compile(r"\[\s*\.\.\.\s*\]|\s\.\.\.\s|…")
_ISSUE_NO = re.compile(r"Kenya Gazette\s+(?:Vol\.\s*[A-Z]+\s*-\s*)?No\.?\s*\d+", re.I)

MAX_EVENT = 320
MOST = 6


def fragments(card_text: str) -> list[str]:
    """The passages a card shows, one by one, as printed."""
    text = _PAGE.sub(" [...] ", card_text or "")
    out = []
    for part in _SPLIT.split(text):
        part = " ".join(part.split()).strip(" .;,")
        if len(part) >= 25:
            out.append(part)
    return out


def _issue_date(card: dict) -> date | None:
    when = card.get("date")
    if isinstance(when, datetime):
        return when.date()
    if isinstance(when, date):
        return when
    m = re.search(r"/(\d{4})-(\d\d)-(\d\d)/", card.get("url") or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _publisher(card: dict, published: date | None) -> str:
    m = _ISSUE_NO.search(card.get("title") or card.get("card_text") or "")
    name = " ".join(m.group(0).split()) if m else "Kenya Gazette"
    return f"{name}, {published.strftime('%-d %B %Y')}" if published else name


def read(cards: list[dict], about, today: date | None = None, most: int = MOST) -> list[dict]:
    """[{date, event, publisher, url, printed, basis, record}], soonest first.

    `cards` are Gazette search cards (`kenya_law_records.parse`, with
    `card_text`); `about` is the issue's matcher (`issue_match.of`)."""
    today = today or date.today()
    out, seen = [], set()
    for card in cards or []:
        published = _issue_date(card)
        for passage in fragments(card.get("card_text") or card.get("snippet") or ""):
            if not about(passage):
                continue
            found: list[tuple[date, str, str]] = []
            if STATED.search(passage):
                for when in upcoming.dates_in(passage, published):
                    if when > today and (published is None or when >= published):
                        found.append((when, "stated_in_source", passage))
            m = RELATIVE.search(passage)
            if m and published:
                days = int(m.group(1))
                when = published + timedelta(days=days)
                if when > today and 0 < days <= 366:
                    found.append((when, "counted_from_publication",
                                  f"{passage} — counted: {days} days from this notice's publication on "
                                  f"{published.strftime('%-d %B %Y')}"))
            for when, basis, event in found:
                key = (when, passage[:80].lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append({"date": when.isoformat(), "event": event[:MAX_EVENT],
                            "publisher": _publisher(card, published), "url": card.get("url"),
                            "printed": published.isoformat() if published else None,
                            "basis": basis, "record": "gazette"})
    out.sort(key=lambda r: r["date"])
    return out[:most]

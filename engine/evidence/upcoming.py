"""Dates ahead, as the sources state them — with no model choosing them.

The Issue Map's sequencing section answers "when do we move?", and it had
nothing to move against: an issue map reads a look-back window, so every
development it finds has already happened, and the section filled itself
with the most recent items instead — on a live map, YouTube comments,
printed as "Move while this is still live: A YouTube commenter…". The
founder's note: "the sequencing section needs to look for future dates like
the next hearing, sitting of the NA committee on commerce etc."

Those dates are in the sources — "the matter will be mentioned on 12
November", "the committee is expected to sit on Tuesday 4 November" — and a
date a source printed is a fact this engine can carry with the source. So
this reads every collected item for a sentence that states a calendar date
AFTER today together with a word naming a scheduled event, and returns the
sentence as printed, cited. Nothing is inferred: a relative date ("next
week") has no anchor anyone can check and is not read; a date with no year
is placed in the year of the item that printed it, or the next one if that
would put it before the item; and a sentence with no event word is not a
decision point, whatever date it carries.
"""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})

#: Words that make a dated sentence a decision point rather than a mention.
EVENT_WORDS = re.compile(
    r"\b(hearing|hear|mention(?:ed)?|ruling|rule|judg(?:e)?ment|verdict|sitting|sit|session|"
    r"committee|tabled?|debate|vote|ballot|deadline|appear|summon(?:ed|s)?|summons|"
    r"meeting|meet|consultation|public participation|launch|sworn|swearing|election|by-election|"
    r"budget reading|reading|petition|application|appeal|directions|trial|plea|inquest|"
    r"sentenc(?:e|ing)|recess|resume|reconvene)\b", re.I)

_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|" \
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(20\d\d))?"
DAY_FIRST = re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}\b{_YEAR}", re.I)
MONTH_FIRST = re.compile(rf"\b{_MONTH}\s+{_DAY}\b{_YEAR}", re.I)
NUMERIC = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")

MAX_SENTENCE = 320
MOST = 12


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _when(day: int, month: int, year: int | None, printed: date | None) -> date | None:
    try:
        if year:
            return date(year, month, day)
        anchor = printed or date.today()
        when = date(anchor.year, month, day)
        return when if when >= anchor else date(anchor.year + 1, month, day)
    except ValueError:
        return None


def dates_in(sentence: str, printed: date | None) -> list[date]:
    found = []
    for m in DAY_FIRST.finditer(sentence):
        month = MONTHS.get(m.group(2).lower()[:3])
        when = _when(int(m.group(1)), month, int(m.group(3)) if m.group(3) else None, printed)
        if when:
            found.append(when)
    for m in MONTH_FIRST.finditer(sentence):
        month = MONTHS.get(m.group(1).lower()[:3])
        when = _when(int(m.group(2)), month, int(m.group(3)) if m.group(3) else None, printed)
        if when:
            found.append(when)
    for m in NUMERIC.finditer(sentence):
        # Kenyan usage is day/month/year.
        when = _when(int(m.group(1)), int(m.group(2)), int(m.group(3)), printed)
        if when:
            found.append(when)
    return found


def scan(corpus: list[dict], today: date | None = None, most: int = MOST) -> list[dict]:
    """[{date, event, publisher, url, ref, printed}], soonest first — each a
    sentence a source printed, naming an event, dated after `today`."""
    from engine.reports import outlets

    today = today or date.today()
    seen: set[tuple] = set()
    out: list[dict] = []
    for item in corpus or []:
        at = item.get("posted_at")
        printed = at.date() if isinstance(at, datetime) else at if isinstance(at, date) else None
        if item.get("date_estimated"):
            printed = None
        for sentence in _sentences(str(item.get("text") or "")):
            if not EVENT_WORDS.search(sentence):
                continue
            for when in dates_in(sentence, printed):
                if when <= today:
                    continue
                key = (when, sentence[:80].lower())
                if key in seen:
                    continue
                seen.add(key)
                out.append({"date": when.isoformat(), "event": sentence[:MAX_SENTENCE],
                            "publisher": outlets.publisher(item), "url": item.get("source_url"),
                            "ref": str(item.get("id") or "")[:8],
                            "printed": printed.isoformat() if printed else None,
                            "basis": "stated_in_source"})
    out.sort(key=lambda r: r["date"])
    return out[:most]

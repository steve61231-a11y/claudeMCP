"""Dates a Kenyan executive reads, not dates a database stores.

The executive brief of a live report opened: "During the 2026-09-15 to
2026-10-15 window, sentiment toward W…". That is the first sentence of the
most-read paragraph in the product, and it is written in the format of a
timestamp column.

It got there honestly. `sections.py` handed the model
`f"{window_start.date()} to {window_end.date()}"`, the model used what it
was given, and nothing downstream looked. Two fixes, because either alone
leaves the hole open:

  - Hand the model British dates in the first place.
  - Rewrite any ISO date that reaches prose anyway. A model quotes dates out
    of the corpus too, and the corpus is full of them.

Kenya writes dates the British way — 15 September 2026, not September 15 —
and an intelligence report that formats them like log output tells a reader
it was generated rather than written.

Deliberately NOT applied to: urls, refs, ids, or anything inside a citation
marker. A date is only reformatted when it is being read as a date.
"""

from __future__ import annotations

import re
from datetime import date, datetime

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

#: A bare ISO date, with nothing url-ish or id-ish touching it.
#:
#: The lookarounds are the whole safety margin: `2026-10-15` inside
#: `nation.africa/2026-10-15/story` or inside `ref=2026-10-15` is a path
#: segment, not a date a reader is reading, and rewriting it breaks a link.
_ISO = re.compile(
    r"(?<![\w/=:.-])(\d{4})-(\d{2})-(\d{2})(?![\w/=.-])"
)


def uk(value) -> str:
    """`date`, `datetime` or ISO string -> "15 September 2026"."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip()[:19])
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def window(start, end) -> str:
    """The phrase handed to a model to describe the report's period.

    One function so the prompt and anything checking the prompt agree, and
    so a second analyst added later cannot quietly reintroduce the ISO form
    by formatting its own.
    """
    return f"{uk(start)} to {uk(end)}"


def britishise(text: str) -> str:
    """Rewrite ISO dates that reached prose anyway.

    The backstop. A model is told the window in words and still quotes
    `2026-10-03` out of a headline it just read, and that sentence goes to
    a client.
    """
    if not text or "-" not in text:
        return text

    def swap(match: re.Match) -> str:
        year, month, day = (int(g) for g in match.groups())
        if not (1 <= month <= 12 and 1 <= day <= 31):
            # Not a date. `2026-99-99` is something else wearing the shape.
            return match.group(0)
        return f"{day} {MONTHS[month - 1]} {year}"

    return _ISO.sub(swap, text)

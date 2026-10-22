"""Can a long report period be collected in full, or only its last weeks?

A one-year Search report asks Google News for `when:365d` and gets at most
about a hundred results, ranked by relevance; GDELT's DOC API is asked for
the whole window. Whether that covers the year, or only its most recent
weeks, decides whether "1 year" is a year. This measures, for a generic
public query:

  * Google News with `when:365d` — the spread of publication months;
  * Google News with explicit `after:` / `before:` month slices — whether
    each slice returns items from that month;
  * GDELT with a start date six and twelve months back — what it returns.

Counts and months only.

    python scripts/period_probe.py
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
QUERY = '"Kenya" parliament'
NOW = datetime.utcnow()


def _note(title: str, line: str) -> None:
    print(f"::notice title={title}::{line}")


def _gn(q: str) -> list[datetime]:
    url = ("https://news.google.com/rss/search?q=" + quote_plus(q)
           + "&hl=en-KE&gl=KE&ceid=KE:en")
    root = ET.fromstring(requests.get(url, headers=UA, timeout=25).content)
    out = []
    for item in root.iter("item"):
        try:
            out.append(parsedate_to_datetime(item.findtext("pubDate")).replace(tzinfo=None))
        except Exception:  # noqa: BLE001
            pass
    return out


def google_news() -> None:
    dates = _gn(f"{QUERY} when:365d")
    months = Counter(d.strftime("%Y-%m") for d in dates)
    _note("Google News when:365d", f"{len(dates)} items; by month {dict(sorted(months.items()))}")
    for back in (1, 3, 6, 9, 11):
        start = (NOW - timedelta(days=30 * back)).date()
        end = start + timedelta(days=30)
        got = _gn(f"{QUERY} after:{start.isoformat()} before:{end.isoformat()}")
        inside = sum(1 for d in got if start <= d.date() <= end)
        _note(f"Google News slice {start}..{end}",
              f"{len(got)} items, {inside} published inside the slice")
        time.sleep(1)


def gdelt() -> None:
    for months in (2, 6, 12):
        start = NOW - timedelta(days=30 * months)
        params = {"query": '"Kenya" parliament', "mode": "artlist", "format": "json",
                  "maxrecords": "250", "sort": "dateasc",
                  "startdatetime": start.strftime("%Y%m%d%H%M%S"),
                  "enddatetime": NOW.strftime("%Y%m%d%H%M%S")}
        body = None
        for _ in range(4):
            try:
                resp = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                                    params=params, headers=UA, timeout=30)
                body = resp.json()
                break
            except Exception:  # noqa: BLE001
                time.sleep(12)
        arts = (body or {}).get("articles") or []
        seen = sorted(a.get("seendate", "")[:8] for a in arts if a.get("seendate"))
        _note(f"GDELT from {months} months back",
              f"{len(arts)} articles; earliest {seen[0] if seen else '-'}, "
              f"latest {seen[-1] if seen else '-'}" if body is not None else "refused every attempt")
        time.sleep(6)


if __name__ == "__main__":
    for probe in (google_news, gdelt):
        try:
            probe()
        except Exception as exc:  # noqa: BLE001
            _note(probe.__name__, f"crashed: {exc}"[:200])

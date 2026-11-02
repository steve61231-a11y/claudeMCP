"""Can the map read a court case's NEXT date? Observed, not assumed.

Round 3. Round 2 found cause-list cards for a case-number search with
"cause list" appended, and the map's strict matcher (same court, number and
year, first party) then found nothing for 30 real cases. This prints what
each phrasing returns for real case numbers, to tell "these cases are not
in any published cause list" from "the query does not surface them".

    python scripts/causelist_probe.py
"""

from __future__ import annotations

import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
API = "https://new.kenyalaw.org/search/api/documents/"
CASES = [("E051", "2026", "Gachoka", "kehc"), ("E001", "2026", "Omtatah", "kehc"), ("E078", "2025", "Abdille", "keelrc"),
         ("E083", "2023", "Ogiek", "keca")]


def show(q: str):
    r = requests.get(API, params={"search": q, "page": 1}, headers=UA, timeout=30)
    html = r.json().get("results_html") or ""
    rows = []
    for card in re.findall(r'<li class="mb-4 hit[^"]*"[^>]*>(.*?)</li>', html, re.S):
        uri = re.search(r'href="(/akn/[^"#]+)"', card)
        snip = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card))
        rows.append((uri.group(1) if uri else "?", snip))
    lists = [x for x in rows if "/doc/cause" in x[0] or "causelist" in x[0]]
    print(f"--- {q!r}: {r.json().get('count')} results; {len(lists)} cause-list cards of {len(rows)}")
    for uri, snip in lists[:4]:
        print(f"   {uri} | {snip[:240]}")


def main() -> int:
    for number, year, party, court in CASES:
        for q in (f"{number}/{year} cause list", f"{number} of {year} {party} cause list", f"{party} cause list",
                  f'"{number}/{year}"'):
            try:
                show(q)
            except Exception as exc:  # noqa: BLE001
                print(f"--- {q!r}: {type(exc).__name__}: {exc}"[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())

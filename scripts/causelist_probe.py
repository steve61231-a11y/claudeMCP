"""Can the map read a court case's NEXT date? Observed, not assumed.

Phase 4 of docs/ISSUE_MAP_FOUNDATION.md: for every case found, its next
hearing. The cause-list pages sit behind a JavaScript challenge, so this
asks the routes a connector could actually use: Kenya Law's search API for
a case number (does it index cause lists?), the cause-list index page, and
one cause list's own URI. Public case numbers only.

    python scripts/causelist_probe.py
"""

from __future__ import annotations

import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
API = "https://new.kenyalaw.org/search/api/documents/"
CASE = re.compile(r"(?:Petition|Appeal|Application|Cause|Suit)\s+(?:No\.?\s*)?E?\d+\s+of\s+\d{4}")
DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+20\d\d\b")


def main() -> int:
    for q in ("Petition E051 of 2026", "E051 of 2026", "cause list", "causelist High Court Constitutional"):
        try:
            r = requests.get(API, params={"search": q, "page": 1}, headers=UA, timeout=30)
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"--- search {q!r}: {type(exc).__name__}: {exc}"[:200]); continue
        html = data.get("results_html") or ""
        uris = re.findall(r'href="(/akn/[^"]+)"', html)
        kinds = {}
        for u in uris:
            k = u.split("/")[3] if len(u.split("/")) > 3 else "?"
            kinds[k] = kinds.get(k, 0) + 1
        print(f"--- search {q!r}: HTTP {r.status_code} count={data.get('count')} kinds={kinds}")
        for u in [u for u in uris if "cause" in u.lower()][:5]:
            print(f"   causelist uri: {u}")
        facets = {k: v for k, v in data.items() if k not in ("results_html",)}
        print(f"   keys: {sorted(facets)[:20]}")
    for url in ("https://new.kenyalaw.org/causelists/", "https://new.kenyalaw.org/causelists/high-court/",
                "https://new.kenyalaw.org/search/api/documents/?search=petition&nature=causelist"):
        try:
            r = requests.get(url, headers=UA, timeout=30)
        except Exception as exc:  # noqa: BLE001
            print(f"--- {url}: {type(exc).__name__}"); continue
        links = sorted(set(re.findall(r'href="(/akn/ke/[^"]*cause[^"]*)"', r.text)))[:6]
        print(f"--- {url}: HTTP {r.status_code} {len(r.content)} bytes; challenge={'challenge' in r.text.lower() or 'cf-' in r.text[:2000]}; causelist links {len(links)}: {links}")
        if links:
            d = requests.get("https://new.kenyalaw.org" + links[0], headers=UA, timeout=30)
            text = re.sub(r"<[^>]+>", " ", d.text)
            cases = CASE.findall(text)[:5]
            dates = DATE.findall(text)[:5]
            print(f"   one list: HTTP {d.status_code} {len(d.content)} bytes; case numbers seen: {cases}")
            print(f"   dates seen: {dates}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

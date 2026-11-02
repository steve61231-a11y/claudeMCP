"""Can the map read a court case's NEXT date? Observed, not assumed.

Round 2. Round 1 (2 Nov 2026) found: cause lists are indexed by the search
API as documents (/akn/ke/doc/cause-list-daily/<court>/<date>/…), future
lists are listed on /causelists/, and a list's own page answers 202 (a
JavaScript challenge). This asks how to restrict the search to cause lists
(the facets the API offers), whether a case number then finds its listing,
and whether a list's /source file is reachable. Public case numbers only.

    python scripts/causelist_probe.py
"""

from __future__ import annotations

import json
import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
API = "https://new.kenyalaw.org/search/api/documents/"


def search(params: dict) -> dict:
    r = requests.get(API, params=params, headers=UA, timeout=30)
    try:
        return {"status": r.status_code, **r.json()}
    except ValueError:
        return {"status": r.status_code, "text": r.text[:200]}


def cards(data: dict) -> list[tuple[str, str]]:
    html = data.get("results_html") or ""
    out = []
    for card in re.findall(r'<li class="mb-4 hit[^"]*"[^>]*>(.*?)</li>', html, re.S):
        uri = re.search(r'href="(/akn/[^"#]+)"', card)
        snip = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", card))[:260]
        out.append((uri.group(1) if uri else "?", snip))
    return out


def main() -> int:
    base = search({"search": "Petition E051 of 2026", "page": 1})
    facets = base.get("facets") or {}
    print("--- facets offered:", json.dumps({k: (list(v)[:12] if isinstance(v, (list, dict)) else v)
                                            for k, v in facets.items()}, default=str)[:1500])
    for params in ({"search": "E051 of 2026", "doc_type": "doc"},
                   {"search": "E051 of 2026", "nature": "Cause List"},
                   {"search": "E051 of 2026", "nature": "cause-list-daily"},
                   {"search": "E051 of 2026", "frbr_doctype": "doc"},
                   {"search": "E051 of 2026 cause list"},
                   {"search": "\"E051 of 2026\" cause-list-daily"}):
        data = search({**params, "page": 1})
        found = cards(data)
        lists = [c for c in found if "cause" in c[0]]
        print(f"--- {params}: HTTP {data.get('status')} count={data.get('count')} cards={len(found)} cause-list cards={len(lists)}")
        for uri, snip in lists[:3]:
            print(f"   {uri} | {snip}")
    for path in ("/akn/ke/doc/cause-list-daily/keca/2026-11-30/1/eng@2026-11-30/source",
                 "/akn/ke/doc/cause-list-daily/keca/2026-11-30/1/eng@2026-11-30/source.pdf",
                 "/akn/ke/doc/cause-list-daily/keca/2026-11-30/1/eng@2026-11-30.pdf"):
        r = requests.get("https://new.kenyalaw.org" + path, headers=UA, timeout=30)
        print(f"--- {path}: HTTP {r.status_code} {r.headers.get('content-type')} {len(r.content)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())

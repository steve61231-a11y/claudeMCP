"""Can the map read a statutory DEADLINE from the Kenya Gazette? Observed, not assumed.

Phase 4 of docs/ISSUE_MAP_FOUNDATION.md: "from the Gazette, statutory
deadlines". Before writing a reader, this prints what the routes return for
public topics only (the repository is public — never a client's subject):

1. Kenya Law's search API (the route the map already reads): which cards are
   Gazette, what their URIs look like, and whether a card's snippet carries
   deadline language ("within N days", "on or before <date>").
2. One Gazette document page and its likely source PDF: reachable, or behind
   the JavaScript challenge the cause-list pages sit behind.

    python scripts/gazette_probe.py
"""

from __future__ import annotations

import re
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
BASE = "https://new.kenyalaw.org"
API = BASE + "/search/api/documents/"
TOPICS = ["Social Health Authority", "Safaricom shares", "public participation", "objections within",
          "Finance Bill", "county health levy", "on or before", "invites comments"]
CARD = re.compile(r'<li class="mb-4 hit[^"]*"[^>]*>(.*?)</li>', re.S)
HREF = re.compile(r'href="(/akn/[^"#]+)"')
DEADLINE = re.compile(r"\bwithin\s+(?:\w+\s+)?\(?\d*\)?\s*days\b|\bon or before\b|\bnot later than\b|\bdeadline\b", re.I)
DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|"
                  r"September|October|November|December),?\s+20\d\d\b")
TAGS = re.compile(r"<[^>]+>")


def flat(html: str) -> str:
    return re.sub(r"\s+", " ", TAGS.sub(" ", html or "")).strip()


def cards(query: str, extra: dict | None = None):
    params = {"search": query, "page": 1}
    params.update(extra or {})
    r = requests.get(API, params=params, headers=UA, timeout=30)
    data = r.json()
    rows = []
    for card in CARD.findall(data.get("results_html") or ""):
        m = HREF.search(card)
        rows.append((m.group(1) if m else "?", flat(card)))
    return r.status_code, data.get("count"), rows


def main() -> int:
    gazette_uris: list[str] = []
    for topic in TOPICS:
        for label, extra in (("plain", {}), ("gazette filter", {"nature": "Gazette"}),
                             ("doc_type", {"doc_type": "Gazette"})):
            try:
                status, count, rows = cards(topic, extra)
            except Exception as exc:  # noqa: BLE001
                print(f"--- {topic!r} [{label}]: {type(exc).__name__}: {exc}"[:200])
                continue
            gz = [x for x in rows if "gazette" in x[0].lower()]
            kinds = sorted({x[0].split("/")[3] if x[0].count("/") > 3 else x[0] for x in rows})
            print(f"--- {topic!r} [{label}]: HTTP {status}, {count} results; {len(gz)} Gazette of {len(rows)} "
                  f"on page 1; kinds {kinds[:6]}")
            for uri, snip in gz[:3]:
                gazette_uris.append(uri)
                print(f"   {uri}")
                print(f"     deadline words: {bool(DEADLINE.search(snip))}; dates: {DATE.findall(snip)[:3]}")
                print(f"     {snip[:300]}")
    print(f"\n=== {len(set(gazette_uris))} distinct Gazette URIs seen")
    for uri in list(dict.fromkeys(gazette_uris))[:2]:
        for path in (uri, uri + "/source", uri + "/source.pdf"):
            try:
                r = requests.get(BASE + path, headers=UA, timeout=30, allow_redirects=True)
                ctype = r.headers.get("content-type", "")
                body = r.content[:4000]
                challenge = b"challenge" in body.lower() or b"just a moment" in body.lower()
                print(f"   GET {path}: HTTP {r.status_code}, {ctype}, {len(r.content)} bytes, challenge={challenge}")
                if "html" in ctype and not challenge:
                    text = flat(r.text)
                    print(f"     deadline words: {len(DEADLINE.findall(text))}; dates: {DATE.findall(text)[:5]}")
                    print(f"     {text[:300]}")
            except Exception as exc:  # noqa: BLE001
                print(f"   GET {path}: {type(exc).__name__}: {exc}"[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())

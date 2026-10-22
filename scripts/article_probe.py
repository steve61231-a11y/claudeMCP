"""How much of the journalism does a run actually read?

Every news item enters the corpus as a headline. `ingestion/article_text.py`
is meant to turn it into the article itself — and the report is only as good
as how often that works, because analysts handed headlines write headlines.
This measures it from GitHub's network, for generic public queries only:

  * Google News items, fetched at the link the connector stores (a
    news.google.com/rss/articles/... address, not the publisher's);
  * the same items after decoding that link to the publisher's own URL;
  * GDELT items, whose links are the publisher's already.

For each: did the fetch succeed, where did it land, and how many characters
of body did trafilatura extract. Prints counts and outlet hosts — no article
text — and writes the same as JSON.

    python scripts/article_probe.py out.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from urllib.parse import quote_plus, urlsplit
from xml.etree import ElementTree as ET

import requests

UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")}
QUERIES = ["Kenya parliament", "Nairobi county", "Kenya economy"]
PER_QUERY = 5
MIN_BODY = 400          # characters: below this it is a headline or a stub


def _note(title: str, line: str) -> None:
    print(f"::notice title={title}::{line}")


def _host(url: str | None) -> str:
    return (urlsplit(url or "").hostname or "").removeprefix("www.")


def _extract(html: str) -> int:
    try:
        import trafilatura
    except Exception:  # noqa: BLE001
        return -1
    try:
        body = trafilatura.extract(html, include_comments=False, include_tables=False,
                                   favor_precision=True) or ""
    except Exception:  # noqa: BLE001
        return 0
    return len(body.strip())


def _fetch(url: str) -> dict:
    """Plain requests first, then Chrome's TLS fingerprint — the two tiers the
    engine's fetch_backend uses."""
    out = {"url_host": _host(url)}
    try:
        resp = requests.get(url, headers=UA, timeout=20, allow_redirects=True)
        out.update(status=resp.status_code, final_host=_host(resp.url),
                   chars=_extract(resp.text) if resp.ok else 0, tier="requests")
        if resp.ok and out["chars"] >= MIN_BODY:
            return out
    except Exception as exc:  # noqa: BLE001
        out.update(status=f"error {type(exc).__name__}", chars=0, tier="requests")
    try:
        from curl_cffi import requests as cffi

        resp = cffi.get(url, impersonate="chrome", timeout=20, allow_redirects=True)
        chars = _extract(resp.text) if resp.status_code < 400 else 0
        if chars > out.get("chars", 0):
            out.update(status=resp.status_code, final_host=_host(str(resp.url)),
                       chars=chars, tier="chrome-fingerprint")
    except Exception as exc:  # noqa: BLE001
        out.setdefault("cffi_error", type(exc).__name__)
    return out


def _decode(link: str) -> str | None:
    try:
        from googlenewsdecoder import gnewsdecoder
    except Exception:  # noqa: BLE001
        return None
    try:
        result = gnewsdecoder(link, interval=1)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(result, dict) and result.get("status"):
        return result.get("decoded_url")
    return None


def google_news(out: dict) -> None:
    items = []
    for q in QUERIES:
        url = ("https://news.google.com/rss/search?q=" + quote_plus(f"{q} when:7d")
               + "&hl=en-KE&gl=KE&ceid=KE:en")
        try:
            root = ET.fromstring(requests.get(url, headers=UA, timeout=25).content)
        except Exception as exc:  # noqa: BLE001
            _note("Google News", f"feed error {exc}"[:200])
            continue
        for item in list(root.iter("item"))[:PER_QUERY]:
            src = item.find("source")
            items.append({"link": item.findtext("link"),
                          "outlet_host": _host(src.get("url") if src is not None else None)})

    as_stored, decoded = [], []
    for it in items:
        stored = _fetch(it["link"])
        as_stored.append(stored)
        target = _decode(it["link"])
        if target:
            got = _fetch(target)
            got["decoded"] = True
            decoded.append(got)
        else:
            decoded.append({"decoded": False, "chars": 0, "outlet_host": it["outlet_host"]})
        time.sleep(1)

    def summary(rows):
        bodies = [r["chars"] for r in rows if (r.get("chars") or 0) >= MIN_BODY]
        return {"items": len(rows), "with_body": len(bodies),
                "median_chars": int(statistics.median(bodies)) if bodies else 0}

    s_stored, s_decoded = summary(as_stored), summary(decoded)
    landed = {}
    for r in as_stored:
        landed[r.get("final_host") or "?"] = landed.get(r.get("final_host") or "?", 0) + 1
    _note("Google News — link as stored",
          f"{s_stored['with_body']} of {s_stored['items']} gave an article body "
          f"(median {s_stored['median_chars']} chars); fetches landed on {landed}")
    _note("Google News — link decoded to the publisher",
          f"{sum(1 for r in decoded if r.get('decoded'))} of {len(decoded)} decoded; "
          f"{s_decoded['with_body']} gave an article body "
          f"(median {s_decoded['median_chars']} chars)")
    for r in decoded:
        print("DECODED\t" + "\t".join(str(r.get(k, "")) for k in
                                      ("decoded", "final_host", "status", "tier", "chars")))
    out["google_news"] = {"as_stored": s_stored, "decoded": s_decoded,
                          "stored_rows": as_stored, "decoded_rows": decoded}


def gdelt(out: dict) -> None:
    params = {"query": '"Kenya" sourcecountry:kenya', "mode": "artlist", "format": "json",
              "maxrecords": "15", "sort": "datedesc", "timespan": "7d"}
    body = None
    for attempt in range(4):
        try:
            resp = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                                params=params, headers=UA, timeout=30)
            body = resp.json()
            break
        except Exception:  # noqa: BLE001
            time.sleep(15)
    arts = (body or {}).get("articles") or []
    rows = [_fetch(a.get("url")) for a in arts[:12] if a.get("url")]
    bodies = [r["chars"] for r in rows if (r.get("chars") or 0) >= MIN_BODY]
    refused = sum(1 for r in rows if isinstance(r.get("status"), int) and r["status"] >= 400)
    _note("GDELT — publisher links",
          f"{len(bodies)} of {len(rows)} gave an article body "
          f"(median {int(statistics.median(bodies)) if bodies else 0} chars); "
          f"{refused} refused with an HTTP error")
    for r in rows:
        print("GDELT\t" + "\t".join(str(r.get(k, "")) for k in
                                    ("url_host", "status", "tier", "chars")))
    out["gdelt"] = {"items": len(rows), "with_body": len(bodies), "rows": rows}


def main() -> int:
    out: dict = {}
    for probe in (google_news, gdelt):
        try:
            probe(out)
        except Exception as exc:  # noqa: BLE001
            _note(probe.__name__, f"crashed: {exc}"[:200])
    path = sys.argv[1] if len(sys.argv) > 1 else "article_probe.json"
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

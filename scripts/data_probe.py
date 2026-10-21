"""What do the free sources actually return — and WHEN was it published?

An investor reading a live report found mentions "going back to the last
election" inside a one-month reporting period. The collectors were written
in a sandbox with no route to any of these sources, so their assumptions
about dates were never checked against the real thing. This runs them from
GitHub's network and measures, per source:

  * how many items carry a publication date at all, and how many of those
    fall BEFORE the reporting period;
  * whether a time restriction in the query (Google News `when:`/`after:`,
    YouTube's date-sorted search) keeps old material out at the source;
  * which outlet names and domains appear, so local and international
    coverage can be told apart from real data rather than a guessed list.

Generic, public queries only. Prints counts and outlet names; writes the
same as JSON for `actions/upload-artifact`.

    python scripts/data_probe.py out.json
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlsplit
from xml.etree import ElementTree as ET

import requests

UA = {"User-Agent": "Mozilla/5.0 (research probe)"}
QUERIES = ['"William Ruto"', "Kenya parliament", "Nairobi county", "Kenya economy",
           "Kenya Senate", "Kenya High Court", "Mombasa", "Kisumu"]
END = datetime.utcnow()
START = END - timedelta(days=30)


def _note(title: str, line: str) -> None:
    print(f"::notice title={title}::{line}")


def google_news(out: dict) -> None:
    outlets: Counter = Counter()
    domains: dict[str, str] = {}
    variants = {
        "plain": "{q}",
        "when30d": "{q} when:30d",
        "when14d": "{q} when:14d",
        "when90d": "{q} when:90d",
        "when365d": "{q} when:365d",
        "when1y": "{q} when:1y",
    }
    results = {}
    for name, template in variants.items():
        total = old = undated = 0
        oldest = None
        for q in QUERIES:
            url = ("https://news.google.com/rss/search?q=" + quote_plus(template.format(q=q))
                   + "&hl=en-KE&gl=KE&ceid=KE:en")
            try:
                root = ET.fromstring(requests.get(url, headers=UA, timeout=25).content)
            except Exception as exc:  # noqa: BLE001
                _note(f"Google News {name}", f"error {exc}"[:200])
                continue
            for item in root.iter("item"):
                total += 1
                try:
                    dt = parsedate_to_datetime(item.findtext("pubDate"))
                    dt = dt.replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    undated += 1
                    continue
                if dt < START:
                    old += 1
                    oldest = min(oldest, dt) if oldest else dt
                src = item.find("source")
                if src is not None and src.text:
                    outlets[src.text.strip()] += 1
                    host = (urlsplit(src.get("url") or "").hostname or "").replace("www.", "")
                    if host:
                        domains[src.text.strip()] = host
        results[name] = {"items": total, "before_period": old, "undated": undated,
                         "oldest": oldest.isoformat() if oldest else None}
        _note(f"Google News {name}", f"{total} items · {old} published before the period "
              f"(oldest {oldest:%Y-%m-%d})" if oldest else f"{total} items · 0 before the period")
    for n, c in outlets.most_common(250):
        print(f"OUTLET\t{c}\t{n}\t{domains.get(n) or ''}")
    out["google_news"] = {"variants": results,
                          "outlets": [{"name": n, "count": c, "domain": domains.get(n)}
                                      for n, c in outlets.most_common(200)]}


def youtube(out: dict) -> None:
    try:
        import yt_dlp
    except Exception as exc:  # noqa: BLE001
        _note("YouTube", f"yt-dlp unavailable: {exc}")
        return
    results = {}
    channels: Counter = Counter()
    filters = {"ytsearch": None, "this_month": "EgIIBA%253D%253D",
               "this_year": "EgIIBQ%253D%253D", "newest_first": "CAI%253D"}
    for prefix, sp in filters.items():
        for approx in (True,):
            opts = {"quiet": True, "extract_flat": True, "skip_download": True}
            if approx:
                opts["extractor_args"] = {"youtubetab": {"approximate_date": [""]}}
            dated = old = total = 0
            oldest = None
            for q in ('"William Ruto"', "Kenya parliament"):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        target = (f"ytsearch20:{q} Kenya" if sp is None else
                                  "https://www.youtube.com/results?search_query="
                                  + quote_plus(f"{q} Kenya") + "&sp=" + sp)
                        info = ydl.extract_info(target, download=False)
                except Exception as exc:  # noqa: BLE001
                    _note("YouTube", f"{prefix} error {exc}"[:200])
                    continue
                for e in (info or {}).get("entries") or []:
                    total += 1
                    channels[e.get("channel") or e.get("uploader") or "?"] += 1
                    dt = None
                    if e.get("timestamp"):
                        dt = datetime.utcfromtimestamp(int(e["timestamp"]))
                    elif e.get("upload_date"):
                        dt = datetime.strptime(e["upload_date"], "%Y%m%d")
                    if dt is None:
                        continue
                    dated += 1
                    if dt < START:
                        old += 1
                        oldest = min(oldest, dt) if oldest else dt
            key = f"{prefix}{'+approximate_date' if approx else ''}"
            results[key] = {"items": total, "dated": dated, "before_period": old,
                            "oldest": oldest.isoformat() if oldest else None}
            _note(f"YouTube {key}", f"{total} videos · {dated} dated · {old} before the period"
                  + (f" (oldest {oldest:%Y-%m-%d})" if oldest else ""))
    # Comments: do they carry their own time?
    comment_report = {}
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True,
                               "extractor_args": {"youtubetab": {"approximate_date": [""]}}}) as ydl:
            info = ydl.extract_info("https://www.youtube.com/results?search_query="
                                    + quote_plus('"William Ruto" Kenya') + "&sp=EgIIBA%253D%253D",
                                    download=False)
        vid = ((info or {}).get("entries") or [{}])[0].get("id")
        if vid:
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "getcomments": True,
                                   "extractor_args": {"youtube": {"max_comments": ["20"]}}}) as ydl:
                full = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            comments = full.get("comments") or []
            stamped = sum(1 for c in comments if c.get("timestamp"))
            comment_report = {"comments": len(comments), "with_timestamp": stamped,
                              "video_upload_date": full.get("upload_date")}
            for c in comments[:20]:
                ts = c.get("timestamp")
                print("COMMENT\t" + (datetime.utcfromtimestamp(ts).isoformat() if ts else "none")
                      + "\t" + str(c.get("_time_text") or ""))
            _note("YouTube comments", f"{len(comments)} comments · {stamped} carry their own "
                  f"timestamp · video uploaded {full.get('upload_date')}")
    except Exception as exc:  # noqa: BLE001
        _note("YouTube comments", f"error {exc}"[:200])
    for n, c in channels.most_common(150):
        print(f"CHANNEL\t{c}\t{n}")
    out["youtube"] = {"variants": results, "comments": comment_report,
                      "channels": [{"name": n, "count": c} for n, c in channels.most_common(80)]}


def gdelt(out: dict) -> None:
    params = {"query": '"William Ruto"', "mode": "artlist", "format": "json",
              "maxrecords": "100", "sort": "datedesc",
              "startdatetime": START.strftime("%Y%m%d%H%M%S"),
              "enddatetime": END.strftime("%Y%m%d%H%M%S")}
    import time

    body = None
    for attempt in range(4):
        resp = requests.get("https://api.gdeltproject.org/api/v2/doc/doc",
                            params=params, headers=UA, timeout=30)
        try:
            body = resp.json()
            break
        except Exception:  # noqa: BLE001
            print(f"GDELT attempt {attempt + 1}: HTTP {resp.status_code} {resp.text[:120]!r}")
            time.sleep(15)
    if body is None:
        _note("GDELT", "refused every attempt")
        return
    arts = body.get("articles") or []
    domains = Counter(a.get("domain") for a in arts)
    old = sum(1 for a in arts if a.get("seendate", "")[:8] < START.strftime("%Y%m%d"))
    countries = Counter(a.get("sourcecountry") for a in arts)
    _note("GDELT", f"{len(arts)} articles · {old} before the period · "
          f"source countries {dict(countries.most_common(6))}")
    for d, c in domains.most_common(150):
        print(f"GDELT_DOMAIN\t{c}\t{d}\t" + str(next((a.get("sourcecountry") for a in arts
                                                         if a.get("domain") == d), "")))
    out["gdelt"] = {"articles": len(arts), "before_period": old,
                    "domains": [{"domain": d, "count": c} for d, c in domains.most_common(120)],
                    "countries": dict(countries)}


def main() -> int:
    out: dict = {"window": [START.isoformat(), END.isoformat()]}
    for probe in (google_news, youtube, gdelt):
        try:
            probe(out)
        except Exception as exc:  # noqa: BLE001
            _note(probe.__name__, f"crashed: {exc}"[:200])
    path = sys.argv[1] if len(sys.argv) > 1 else "data_probe.json"
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Can a cloud server reach each free source — and through which door?

Run by `.github/workflows/source-probe.yml` on GitHub's own runners, which sit
on a datacenter network the way the production container does. The engine's
development sandbox cannot reach kenyalaw.org or reddit.com at all, so without
this every fix for a refused source was written blind, and the first test of
it was a client's report.

For each endpoint it tries the two doors the engine uses — plain `requests`
with the engine's User-Agent, and `curl_cffi` presenting Chrome's TLS
fingerprint — and prints one GitHub annotation per attempt: status, size,
whether a bot wall answered. Nothing here needs a secret, and nothing it
prints names a subject anyone is researching.

    python scripts/source_probe.py
"""

from __future__ import annotations

import os
import sys

import requests

ENGINE_UA = ("Muugi/1.0 (+https://github.com/steve61231-a11y/claudemcp; "
             "political research) python-requests")
REDDIT_UA = "zenith-intel/1.0 (political sentiment research)"

PROBES = [
    ("Kenya Law judgments", "https://new.kenyalaw.org/search/?q=Ruto&page=1", ENGINE_UA),
    ("Kenya Gazette", "https://new.kenyalaw.org/search/?q=Ruto&doc_type=Gazette&page=1", ENGINE_UA),
    ("Kenya Law home", "https://new.kenyalaw.org/", ENGINE_UA),
    ("Reddit keyless", "https://www.reddit.com/r/Kenya/search.json?q=Ruto&restrict_sr=1&limit=5", REDDIT_UA),
    ("Reddit old keyless", "https://old.reddit.com/r/Kenya/search.json?q=Ruto&restrict_sr=1&limit=5", REDDIT_UA),
    ("Reddit OAuth host (401 = reachable, needs a token)", "https://oauth.reddit.com/r/Kenya/search?q=Ruto&limit=5", REDDIT_UA),
    ("GDELT", "https://api.gdeltproject.org/api/v2/doc/doc?query=Ruto&mode=ArtList&format=json&maxrecords=5", ENGINE_UA),
    ("Google News RSS", "https://news.google.com/rss/search?q=Ruto&hl=en-KE&gl=KE&ceid=KE:en", ENGINE_UA),
    # Search demand's keyless rungs (engine/evidence/search_sources.py), asked
    # about a public topic, never a subject: does Google answer a cloud
    # address, and does the suggestion box?
    ("Google Trends explore (direct)",
     "https://trends.google.com/trends/api/explore?hl=en-US&tz=0&req=%7B%22comparisonItem%22%3A%20%5B%7B%22keyword%22%3A%20%22finance%20bill%22%2C%20%22time%22%3A%20%22today%201-m%22%2C%20%22geo%22%3A%20%22KE%22%7D%5D%2C%20%22category%22%3A%200%2C%20%22property%22%3A%20%22%22%7D", ENGINE_UA),
    ("Google search suggestions (Kenya)",
     "https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=ke&q=finance%20bill", ENGINE_UA),
    ("Wikipedia page views",
     "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Kenya/daily/20261001/20261020",
     ENGINE_UA),
]

WALL_MARKERS = ("just a moment", "cf-browser-verification", "cf_chl_opt",
                "attention required! | cloudflare", "access denied",
                "enable javascript and cookies to continue", "blocked")


def _describe(status, body: str, headers) -> str:
    head = (body or "")[:6000].lower()
    wall = [m for m in WALL_MARKERS if m in head]
    cf = "cf-ray" in {k.lower() for k in (headers or {})}
    return (f"HTTP {status} · {len(body or '')} bytes"
            f"{' · Cloudflare' if cf else ''}"
            f"{' · wall: ' + ', '.join(wall) if wall else ''}")


def _plain(url: str, ua: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=25)
        return _describe(r.status_code, r.text, r.headers)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {str(exc)[:120]}"


def _chrome(url: str) -> str:
    try:
        from curl_cffi import requests as curl
    except Exception as exc:  # noqa: BLE001
        return f"curl_cffi unavailable: {exc}"
    try:
        r = curl.get(url, impersonate="chrome", timeout=25,
                     headers={"Accept-Language": "en-GB,en;q=0.9"})
        return _describe(r.status_code, r.text, r.headers)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {str(exc)[:120]}"


def main() -> int:
    for label, url, ua in PROBES:
        plain = _plain(url, ua)
        chrome = _chrome(url)
        line = f"plain requests → {plain}  |  Chrome fingerprint → {chrome}"
        print(f"::notice title={label}::{line}")
        # GitHub keeps ten notice annotations per step and drops the rest
        # without a word — the eleventh probe vanished. The job summary keeps
        # every line.
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"- **{label}**: {line}\n")
        print(f"{label}: {line}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

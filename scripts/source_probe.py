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

#: The Issue Map's primary records (docs/ISSUE_MAP_FOUNDATION.md Phase 1),
#: asked about public topics only. For these the SHAPE of the answer is
#: printed too — JSON keys and the first row's keys, or an HTML page's title
#: and how many documents it links — because a connector is written against
#: what a source returns, never against what it is assumed to return.
RECORD_PROBES = [
    ("Mzalendo API root", "https://info.mzalendo.com/api/v0.1/?format=json", ENGINE_UA),
    ("Mzalendo Hansard entries", "https://info.mzalendo.com/api/v0.1/hansard/entries/?format=json", ENGINE_UA),
    ("Mzalendo Hansard sittings", "https://info.mzalendo.com/api/v0.1/hansard/sittings/?format=json", ENGINE_UA),
    ("Mzalendo Hansard search page", "https://info.mzalendo.com/hansard/search/?q=health", ENGINE_UA),
    ("Kenya Law search API", "https://new.kenyalaw.org/search/api/documents/?search=health%20insurance&format=json", ENGINE_UA),
    ("Kenya Law cause lists", "https://new.kenyalaw.org/causelists/", ENGINE_UA),
    ("Kenya Law High Court cause lists", "https://new.kenyalaw.org/causelists/KEHC/", ENGINE_UA),
    ("Kenya Law gazettes", "https://new.kenyalaw.org/gazettes/", ENGINE_UA),
    ("PPIP OCDS page", "https://tenders.go.ke/ocds", ENGINE_UA),
    ("PPIP API guess", "https://tenders.go.ke/api/ocds/releases?page=1", ENGINE_UA),
    ("ICIJ Offshore Leaks search", "https://offshoreleaks.icij.org/search?q=Safaricom", ENGINE_UA),
    ("Wikidata SPARQL",
     "https://query.wikidata.org/sparql?format=json&query=SELECT%20%3Fh%20WHERE%20%7Bwd%3AQ114%20wdt%3AP35%20%3Fh%7D",
     ENGINE_UA),
    ("World Bank projects API",
     "https://search.worldbank.org/api/v2/projects?format=json&countrycode_exact=KE&rows=2", ENGINE_UA),
    ("World Bank documents API",
     "https://search.worldbank.org/api/v2/wds?format=json&qterm=Kenya%20health&rows=2", ENGINE_UA),
    ("IMF Kenya page", "https://www.imf.org/en/Countries/KEN", ENGINE_UA),
    ("Parliament home", "https://www.parliament.go.ke/", ENGINE_UA),
    ("Parliament order papers",
     "https://www.parliament.go.ke/the-national-assembly/house-business/order-paper", ENGINE_UA),
    ("Parliament Hansard listing",
     "https://www.parliament.go.ke/the-national-assembly/house-business/hansard", ENGINE_UA),
]

#: Read further: a snippet of the body, for the contracts the shape alone
#: does not settle — how a list filters and orders, what a cause list holds
#: inside, what an order paper links. Public topics only.
#: Round three (2 Nov): the corrected contracts, the cause list through
#: Chrome's fingerprint, and whether a Parliament PDF downloads whole.
CHROME_SNIPPETS = [
    ("Kenya Law search API, search=", "https://new.kenyalaw.org/search/api/documents/?search=health%20insurance"),
    ("A High Court cause list (Chrome)",
     "https://new.kenyalaw.org/akn/ke/doc/cause-list-daily/kehc/2026-09-16/1/eng@2026-09-16"),
    ("A gazette (Chrome)", "https://new.kenyalaw.org/akn/ke/officialGazette/2026-01-02/1/eng@2026-01-02"),
    ("Mzalendo sittings, last page", "https://info.mzalendo.com/api/v0.1/hansard/sittings/?format=json&page=369"),
]
PDF_PROBE = ("Parliament Hansard PDF",
             "https://www.parliament.go.ke/sites/default/files/2026-10/"
             "The%20Hansard%20-%20Thursday%2C%2022%20October%202026_0.pdf")

SNIPPET_PROBES = [
    ("Mzalendo entries, search=", "https://info.mzalendo.com/api/v0.1/hansard/entries/?format=json&search=health"),
    ("Mzalendo entries, newest", "https://info.mzalendo.com/api/v0.1/hansard/entries/?format=json&ordering=-id"),
    ("Mzalendo sittings, newest",
     "https://info.mzalendo.com/api/v0.1/hansard/sittings/?format=json&ordering=-start_date"),
    ("Mzalendo entries browsable", "https://info.mzalendo.com/api/v0.1/hansard/entries/?format=api"),
    ("Kenya Law search API (400 body)",
     "https://new.kenyalaw.org/search/api/documents/?search=health%20insurance&format=json"),
    ("Kenya Law search API, q=", "https://new.kenyalaw.org/search/api/documents/?q=health%20insurance"),
    ("A High Court cause list",
     "https://new.kenyalaw.org/akn/ke/doc/cause-list-daily/kehc/2026-09-16/1/eng@2026-09-16"),
    ("Gazettes 2026", "https://new.kenyalaw.org/gazettes/2026"),
    ("Parliament order papers (document links)",
     "https://www.parliament.go.ke/the-national-assembly/house-business/order-paper"),
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


def _shape(url: str, ua: str) -> str:
    """What the answer looks like: JSON keys and the first row's, or a
    page's title and the documents it links."""
    import json
    import re

    try:
        r = requests.get(url, headers={"User-Agent": ua, "Accept": "application/json, text/html"}, timeout=25)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    text = r.text or ""
    try:
        data = json.loads(text)
    except ValueError:
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        pdfs = len(re.findall(r"href=\"[^\"]+\.(?:pdf|docx?)\"", text, re.I))
        links = re.findall(r'href="(/[^"#?]{3,80})"', text)
        sample = sorted({l for l in links if l.count("/") >= 2})[:12]
        return (f"HTML title={(title.group(1).strip()[:90] if title else None)!r} pdf/doc links={pdfs} "
                f"sample paths={sample}")

    def keys(node, depth=0):
        if isinstance(node, dict):
            out = []
            for k, v in list(node.items())[:25]:
                out.append(k if depth >= 1 else f"{k}:{keys(v, depth + 1)}")
            return "{" + ", ".join(out) + "}"
        if isinstance(node, list):
            return f"[{len(node)}x {keys(node[0], depth) if node else ''}]"
        return type(node).__name__
    return f"JSON {keys(data)}"[:1500]


def _snippet(url: str) -> str:
    import re

    try:
        r = requests.get(url, headers={"User-Agent": ENGINE_UA}, timeout=25)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    body = r.text or ""
    docs = re.findall(r'href="([^"]+\.(?:pdf|docx?))"', body, re.I)[:6]
    akn = sorted(set(re.findall(r'href="(/akn/[^"]+)"', body)))[:8]
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    start = max(0, text.lower().find("cause list") - 50) if "cause-list" in url else 0
    return (f"HTTP {r.status_code} · docs={docs} akn={akn} · text: {text[start:start + 900]}")


def _chrome_snippet(url: str) -> str:
    import re

    try:
        from curl_cffi import requests as curl

        r = curl.get(url, impersonate="chrome", timeout=30, headers={"Accept-Language": "en-GB,en;q=0.9"})
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}: {str(exc)[:100]}"
    body = r.text or ""
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for marker in ("CAUSE LIST", "Cause List", "GAZETTE NOTICE", "results"):
        at = text.find(marker)
        if at > 0:
            text = text[at:]
            break
    return f"HTTP {r.status_code} · {len(body)} bytes · text: {text[:1100]}"


def _pdf(url: str) -> str:
    try:
        r = requests.get(url, headers={"User-Agent": ENGINE_UA}, timeout=60)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    head = r.content[:8]
    try:
        import io

        from pypdf import PdfReader

        pages = PdfReader(io.BytesIO(r.content)).pages
        first = (pages[1].extract_text() or "")[:600] if len(pages) > 1 else ""
        extra = f" · {len(pages)} pages · page 2 text: {' '.join(first.split())}"
    except Exception as exc:  # noqa: BLE001
        extra = f" · not parsed ({type(exc).__name__})"
    return f"HTTP {r.status_code} · {len(r.content)} bytes · starts {head!r} · {r.headers.get('content-type')}{extra}"


#: Round four: the exact contracts the connectors are written against.
ROUND4 = [
    ("KL search JSON keys + first result HTML",
     "https://new.kenyalaw.org/search/api/documents/?search=insurance%20levy"),
    ("KL search, cause lists?",
     "https://new.kenyalaw.org/search/api/documents/?search=insurance&nature=Cause%20List"),
    ("KL search, page 2", "https://new.kenyalaw.org/search/api/documents/?search=insurance&page=2"),
    ("Senate Hansard listing", "https://www.parliament.go.ke/the-senate/house-business/hansard"),
    ("NA Hansard listing page 2",
     "https://www.parliament.go.ke/the-national-assembly/house-business/hansard?page=1"),
    ("Senate order papers", "https://www.parliament.go.ke/the-senate/house-business/order-paper"),
]


def _round4(url: str) -> str:
    import json
    import re

    try:
        r = requests.get(url, headers={"User-Agent": ENGINE_UA}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"
    try:
        data = json.loads(r.text)
        keys = {k: (type(v).__name__, (len(v) if hasattr(v, "__len__") else v)) for k, v in data.items()}
        html = data.get("results_html") or ""
        first = html[:2200]
        facets = data.get("facets")
        return (f"HTTP {r.status_code} · keys={keys} · facets={str(facets)[:700]} · "
                f"first result HTML: {' '.join(first.split())}")
    except ValueError:
        docs = re.findall(r'href="([^"]+\.pdf)"', r.text, re.I)
        pager = re.findall(r'href="([^"]*\?page=\d+)"', r.text)[:4]
        return f"HTTP {r.status_code} · pdfs={len(docs)} first={docs[:3]} pager={pager}"


def main() -> int:
    for label, url in ROUND4:
        print(f"ROUND4 {label}: {_round4(url)}", file=sys.stderr)
    for label, url in CHROME_SNIPPETS:
        print(f"ROUND3 {label}: {_chrome_snippet(url)}", file=sys.stderr)
    print(f"ROUND3 {PDF_PROBE[0]}: {_pdf(PDF_PROBE[1])}", file=sys.stderr)
    for label, url in SNIPPET_PROBES:
        print(f"SNIPPET {label}: {_snippet(url)}", file=sys.stderr)
    for label, url, ua in RECORD_PROBES:
        plain = _plain(url, ua)
        shape = _shape(url, ua) if plain.startswith("HTTP 2") else ""
        line = f"plain requests → {plain}" + (f"  |  shape → {shape}" if shape else
                                               f"  |  Chrome fingerprint → {_chrome(url)}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(f"- **{label}**: {line}\n")
        print(f"RECORD {label}: {line}", file=sys.stderr)
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

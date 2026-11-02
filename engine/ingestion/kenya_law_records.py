"""Kenya Law's search API, read for the Issue Map: cases, parties, case numbers.

A connection between a person and an issue is made on paper, and the court
record is the paper a client can least argue with: who petitioned whom, in
which court, under which case number, on which date. The Search report's
Kenya Law connector (`kenya_law_connector.py`) was written without network
access and reads the HTML search page; this module is the Issue Map's own,
written against the contract the source probe OBSERVED on 2 Nov 2026 from
GitHub's network and from the live server (`scripts/source_probe.py`,
`evidence/record_reach.py`):

    GET https://new.kenyalaw.org/search/api/documents/?search=<terms>&page=<n>
    -> {"count": int, "results_html": "<ul …><li class="mb-4 hit" …>…", …}

Each hit is a card: a title link whose `href` is the document's Akoma Ntoso
URI (`/akn/ke/judgment/kehc/2024/8429/eng@2024-07-10` — the type, the court
and the date are in it), an italic alternative title, the date and the
court in `span.me-3`, and a snippet of the source's own words with the
search terms in `<mark>`. `format=json` is REFUSED (400) — the endpoint
answers JSON by default — and `nature=` filters nothing; the probe found
both. The document pages themselves sit behind a JavaScript challenge, so
the record is the card: title, parties, case number, court, date, snippet.

Nothing is inferred. Parties are read from the title's "A v B" form only;
a title without one has no parties, not guessed ones.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import unescape

from engine.ingestion import http
from engine.ingestion.base import IngestedMention

BASE = "https://new.kenyalaw.org"
SEARCH = f"{BASE}/search/api/documents/"

#: The URI's document type -> the corpus source_type. Judgments and the
#: Gazette keep the Search report's names, so every consumer that already
#: knows a primary record (quality, the evidence sample) knows these.
DOC_TYPES = {"judgment": "judgment", "officialGazette": "gazette", "bill": "bill", "act": "legislation",
             "doc": "record"}

_CARD = re.compile(r'<li class="mb-4 hit[^"]*"[^>]*>(.*?)</li>', re.S)
_TITLE = re.compile(r'<h5 class="card-title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SPANS = re.compile(r'<span class="me-3">\s*(.*?)\s*</span>', re.S)
_SNIPPET = re.compile(r'<div class="ms-3 snippet text-muted">(.*?)</div>|<span>\s*(\[\.\.\.\].*?)</span>', re.S)
_URI = re.compile(r"^/akn/ke/([A-Za-z]+)/(?:([a-z0-9-]+)/)?")
#: "(Constitutional Petition E015 of 2022)", "(Civil Appeal 12 of 2021)".
_CASE_NO = re.compile(r"\(((?:(?:[A-Z][A-Za-z]*|and|of|&)\s+){0,7}(?:Petition|Appeal|Application|Suit|Cause|Case|"
                      r"Reference|Revision|Complaint|Claim|Judicial Review|Miscellaneous)[^()]*?\d{4})\)")
_DATE = re.compile(r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|"
                   r"November|December)\s+(\d{4})\b")


def _text(html: str) -> str:
    # <mark> highlights are inside words' runs: "of <mark>Insurance</mark>;"
    # must read "of Insurance;", not "of Insurance ;".
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", html or "")).split())
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+([;,.:)])", r"\1", text)


def parties(title: str) -> list[str]:
    """The parties of an "A v B" title, as printed — "& 4 others" kept as
    words, interested parties after ";" listed in their own right. A title
    with no "v" has none."""
    head = re.split(r"\s+\(|\s+\[", title, maxsplit=1)[0]
    if " v " not in head:
        return []
    out: list[str] = []
    for side in head.split(" v ", 1):
        for part in side.split(";"):
            name = re.sub(r"\s*\((?:Interested Part(?:y|ies)|Respondent|Petitioner|Applicant)s?\)\s*$",
                          "", part.strip(), flags=re.I).strip(" ,")
            if name:
                out.append(name)
    return out


def case_number(title: str) -> str | None:
    m = _CASE_NO.search(title or "")
    return " ".join(m.group(1).split()) if m else None


def parse(results_html: str) -> list[dict]:
    """The cards of one results page, as plain records."""
    out = []
    for card in _CARD.findall(results_html or ""):
        m = _TITLE.search(card)
        if not m:
            continue
        href, title = m.group(1), _text(m.group(2))
        uri = _URI.match(href)
        kind = DOC_TYPES.get(uri.group(1), "record") if uri else "record"
        spans = [_text(x) for x in _SPANS.findall(card)]
        date = None
        for span in spans:
            d = _DATE.search(span)
            if d:
                date = datetime.strptime(" ".join(d.groups()), "%d %B %Y")
                break
        body = next((s for s in spans if not _DATE.search(s)), None)
        snip = _SNIPPET.search(card)
        snippet = _text((snip.group(1) or snip.group(2)) if snip else "")
        out.append({"title": title, "url": BASE + href, "kind": kind, "court": body,
                    "court_code": uri.group(2) if uri else None, "date": date,
                    "parties": parties(title), "case_number": case_number(title), "snippet": snippet})
    return out


def search(query: str, page: int = 1) -> tuple[list[dict], int, str | None]:
    """(records, total count, why-nothing) for one query page. The third
    value says WHICH nothing: refused, unparsable, or genuinely none."""
    try:
        response = http.get(SEARCH, params={"search": query, "page": page}, timeout=30)
    except http.HostRefused as exc:
        return [], 0, str(exc)
    except Exception as exc:  # noqa: BLE001
        return [], 0, f"{type(exc).__name__}: {exc}"[:200]
    if response.status_code in (401, 403, 202):
        reason = f"HTTP {response.status_code}: Kenya Law refused this server"
        if response.status_code in (401, 403):
            http.remember_refusal(SEARCH, reason)
        return [], 0, reason
    if response.status_code != 200:
        return [], 0, f"HTTP {response.status_code}"
    try:
        data = response.json()
    except ValueError:
        return [], 0, "the answer was not JSON — the contract has changed"
    count = int(data.get("count") or 0)
    records = parse(data.get("results_html") or "")
    if count and not records:
        return [], count, "results were counted but none parsed — the card layout has changed"
    return records, count, None


def as_mention(record: dict) -> IngestedMention:
    """A record as a corpus item: the card's own words, dated by the record,
    marked primary so every consumer reserves it a place."""
    bits = [record["title"]]
    if record.get("court") or record.get("date"):
        bits.append(", ".join(x for x in (record.get("court"),
                                          record["date"].strftime("%d %B %Y") if record.get("date") else None) if x))
    if record.get("snippet"):
        bits.append(record["snippet"])
    return IngestedMention(
        platform="kenyalaw.org", source_type=record["kind"],
        author_handle=record.get("court") or "Kenya Law",
        text=". ".join(bits)[:4000], posted_at=record.get("date") or datetime.utcnow(),
        engagement={},
        raw_payload={"url": record["url"], "record": "primary", "source": "kenya_law_search",
                     "parties": record.get("parties") or [], "case_number": record.get("case_number"),
                     "court_code": record.get("court_code"), "date_estimated": record.get("date") is None},
    )

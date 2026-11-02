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
#: "(Constitutional Petition E015 of 2022)", "(Civil Appeal 12 of 2021)",
#: and a consolidated cause with its own bracket inside: "(Petition E051 of
#: 2026 & E836 & E831 of 2025 (Consolidated))" — which the first pattern
#: missed, so the page showed a whole title where a case number belonged.
_CASE_NO = re.compile(r"\(((?:(?:[A-Z][A-Za-z]*|and|of|&)\s+){0,7}(?:Petition|Appeal|Application|Suit|Cause|Case|"
                      r"Reference|Revision|Complaint|Claim|Judicial Review|Miscellaneous)\b"
                      r"(?:[^()]|\(Consolidated[^()]*\))*?\d{4}(?:\s*\(Consolidated\))?)\)")
_CASE_KEY = re.compile(r"\b(Petition|Appeal|Application|Suit|Cause|Case|Reference|Revision|Complaint|Claim|"
                       r"Judicial Review|Miscellaneous)\b(?:\s+No\.?)?\s+([A-Z]{0,3}\s?\d+)[^()]*?\bof\s+(\d{4})")
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
    # The parties end where the case number or the citation begins — not at
    # the first bracket, which cut "H. E. (Former) President Uhuru Muigai
    # Kenyatta" to "H. E." and made a former president a private person.
    number = _CASE_NO.search(title or "")
    head = (title[:number.start()] if number else title or "")
    head = re.split(r"\s+\[|\s+\((?:Ruling|Judgment|KLR)\)", head, maxsplit=1)[0]
    if " v " not in head:
        return []
    out: list[str] = []
    for side in head.split(" v ", 1):
        for part in side.split(";"):
            name = re.sub(r"\s*\((?:Interested Part(?:y|ies)|Respondent|Petitioner|Applicant)s?\)\s*$",
                          "", part.strip(), flags=re.I)
            name = re.sub(r"\s*\((?:Suing|Sued|On behalf|Being)[^()]*\)", "", name, flags=re.I).strip(" ,")
            if name:
                out.append(name)
    return out


def sides(title: str) -> dict[str, list[str]]:
    """Who is on which side, as the title prints it: the party or parties
    before " v " brought the matter; those after it answer it; those after
    a ";" marked "(Interested Party/Parties)" are interested parties. A
    title with no " v " ("In re …", a Bill, a Gazette) has no sides."""
    number = _CASE_NO.search(title or "")
    head = (title[:number.start()] if number else title or "")
    head = re.split(r"\s+\[|\s+\((?:Ruling|Judgment|KLR)\)", head, maxsplit=1)[0]
    if " v " not in head:
        return {}
    left, right = head.split(" v ", 1)
    out: dict[str, list[str]] = {"brought": [], "answered": [], "interested": []}

    def tidy(part: str) -> str:
        part = re.sub(r"\s*\((?:Suing|Sued|On behalf|Being)[^()]*\)", "", part, flags=re.I)
        return part.strip(" ,;")

    for part in left.split(";"):
        if tidy(part):
            out["brought"].append(tidy(part))
    for i, part in enumerate(right.split(";")):
        interested = re.search(r"\(Interested Part(?:y|ies)\)\s*$", part, re.I)
        name = tidy(re.sub(r"\s*\((?:Interested Part(?:y|ies)|Respondent|Petitioner|Applicant)s?\)\s*$",
                           "", part, flags=re.I))
        if name:
            out["interested" if interested or i > 0 else "answered"].append(name)
    return out


def case_number(title: str) -> str | None:
    m = _CASE_NO.search(title or "")
    return " ".join(m.group(1).split()) if m else None


def citation(title: str) -> str | None:
    """The neutral citation — "[2026] KEHC 8409 (KLR)" — which names one
    ruling where the case number names the whole cause."""
    m = re.search(r"\[\d{4}\]\s+[A-Z]{2,8}\s+\d+\s+\(KLR\)", title or "")
    return m.group(0) if m else None


def case_key(number: str | None, court_code: str | None = None) -> str | None:
    """One case however many rulings it has: "Petition E051 of 2026", from
    "Petition E051 of 2026 & E836 & E831 of 2025 (Consolidated)" and from
    "Petition E051, E836 & E831 of 2026 (Consolidated)" alike. A live map
    counted one consolidated petition's four rulings as four cases."""
    m = _CASE_KEY.search(number or "")
    if not m:
        return None
    key = f"{m.group(1)} {m.group(2).replace(' ', '')} of {m.group(3)}"
    return f"{key} ({court_code})" if court_code else key


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
                     "title": record["title"], "court": record.get("court"),
                     "parties": record.get("parties") or [], "sides": sides(record["title"]),
                     "case_number": record.get("case_number"),
                     "case_key": case_key(record.get("case_number"), record.get("court_code")),
                     "citation": citation(record["title"]),
                     "court_code": record.get("court_code"), "listings": record.get("listings") or [],
                     "date_estimated": record.get("date") is None},
    )


# ---------------------------------------------------------------------------
# A case's dates in the cause lists (docs/ISSUE_MAP_FOUNDATION.md Phase 4)
# ---------------------------------------------------------------------------
#
# Observed (scripts/causelist_probe.py, 2 Nov 2026): Kenya Law indexes the
# courts' daily cause lists as documents —
# /akn/ke/doc/cause-list-daily/<court>/<date>/… — including lists for dates
# ahead; a list's own page and its source file answer 202 (a JavaScript
# challenge), and the search API has no filter for them, but a search for a
# case number with "cause list" returns their cards, each with the list's
# date in its URI and the listing in its snippet. The same probe found the
# danger: "E051 of 2026 cause list" returned an HIV and AIDS Tribunal list
# carrying "HATC/E051/2026" — a different case in a different court. A wrong
# hearing date in a client's file is worse than none, so a listing counts
# only when the list is the SAME court's, prints the same number and year,
# and names the case's first party.

_LIST_URI = re.compile(r"^/akn/ke/doc/(cause-list[a-z-]*|decongestion-causelist)/([a-z0-9-]+)/(\d{4}-\d{2}-\d{2})/")


def _number_parts(case_key_text: str) -> tuple[str, str] | None:
    m = re.search(r"\b([A-Z]{0,3}\s?\d+)\s+of\s+(\d{4})", case_key_text or "")
    return (m.group(1).replace(" ", ""), m.group(2)) if m else None


def _lists_number(snippet: str, number: str, year: str) -> bool:
    """The listing prints this number and year, in the forms the lists use:
    "E051 of 2026", "E051/2026", "PET/E051/2026", "E051 / 2026"."""
    n = re.escape(number)
    return bool(re.search(rf"(?<![A-Za-z0-9]){n}\s*(?:/|of)\s*{year}\b", snippet, re.I))


def _first_party_word(parties: list[str]) -> str | None:
    """The first party's distinctive word — "Gachoka" from "Gachoka & 3
    others". Initials and offices are no help in matching a listing."""
    for word in re.findall(r"[A-Za-z][A-Za-z'’-]{3,}", (parties or [""])[0]):
        if word.lower() not in {"others", "another", "republic", "cabinet", "secretary", "the", "and",
                                "attorney", "general", "county", "government", "national", "kenya"}:
            return word
    return None


def listings(case_number_text: str, court_code: str | None, parties: list[str]) -> tuple[list[dict], str | None]:
    """(the dates this case is listed, why-nothing). Only listings that pass
    all three tests: same court, same number and year, first party named."""
    parts = _number_parts(case_number_text)
    party = _first_party_word(parties)
    if not parts or not court_code or not party:
        return [], "the case number, court or a party to match on is missing"
    number, year = parts
    try:
        response = http.get(SEARCH, params={"search": f"{number} of {year} {party} cause list", "page": 1},
                            timeout=30)
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {exc}"[:200]
    if response.status_code != 200:
        return [], f"HTTP {response.status_code}"
    try:
        html = (response.json() or {}).get("results_html") or ""
    except ValueError:
        return [], "the answer was not JSON — the contract has changed"
    out, seen = [], set()
    for card in _CARD.findall(html):
        m = _TITLE.search(card)
        uri = _LIST_URI.match(m.group(1)) if m else None
        if not uri or uri.group(2) != court_code:
            continue
        snip = _SNIPPET.search(card)
        snippet = _text((snip.group(1) or snip.group(2)) if snip else "")
        if not _lists_number(snippet, number, year) or party.lower() not in snippet.lower():
            continue
        day = uri.group(3)
        if day in seen:
            continue
        seen.add(day)
        out.append({"date": day, "list": _text(m.group(2)), "url": BASE + m.group(1).split("#")[0],
                    "snippet": snippet[:400]})
    return sorted(out, key=lambda x: x["date"]), None

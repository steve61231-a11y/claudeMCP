"""Parliament's own record, read for the Issue Map: who said what, and what is next.

The Hansard is where an issue is argued by the people who decide it, on the
record, by name, and the order paper is where the next sitting on it is
announced. Mzalendo's Hansard API was the obvious route and the probe ruled
it out: it ignores `search` and `ordering`, serves 1.6 million speeches
oldest first, and its newest sitting is July 2025. Parliament's own site is
current — the 22 October 2026 sitting was online within days, 88 pages of
extractable text — and reachable from the live server
(`evidence/record_reach.py`).

Observed contract (scripts/source_probe.py, 2 Nov 2026): every
parliament.go.ke house-business page carries the same block of the ~29 most
recent documents — Hansards ("The Hansard - Thursday, 22 October 2026_0.pdf")
and order papers ("ORDER PAPER FOR THURSDAY, 22ND OCTOBER 2026.pdf") — and
`?page=` changes nothing. So this reads about the last two weeks of sittings
and the order papers ahead; the full archive (Parliament's library) is a
later connector, said rather than implied.

What it returns is the source's own words: a speech is the speaker's turn as
printed, under the speaker's name as printed, dated by the sitting; an
agenda item is the order paper's line. Nothing is summarised here.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from urllib.parse import unquote, urljoin

from engine.ingestion import http
from engine.ingestion.base import IngestedMention

LISTINGS = (
    "https://www.parliament.go.ke/the-national-assembly/house-business/hansard",
    "https://www.parliament.go.ke/the-national-assembly/house-business/order-paper",
)
MAX_HANSARDS = 8
MAX_ORDER_PAPERS = 6
MAX_TURNS_PER_SITTING = 6
MAX_TURN_CHARS = 2500
MAX_PDF_BYTES = 15_000_000

_PDF = re.compile(r'href="([^"]+\.pdf)"', re.I)
#: A speaker's label at the start of a turn, as the Hansard prints it.
_SPEAKER = re.compile(
    r"(?:^|\n)\s*((?:Hon\.|Sen\.)\s+[A-Z][^:\n]{1,90}?|The (?:Temporary |Deputy )?Speaker[^:\n]{0,60}?"
    r"|The (?:Temporary )?Chairperson[^:\n]{0,60}?)\s*:\s", re.M)


def _name(url: str) -> str:
    return unquote(url.rsplit("/", 1)[-1])


def documents(html: str, base: str) -> list[dict]:
    """The listing's documents, each with its kind and the date its name gives."""
    from engine.evidence import upcoming

    out, seen = [], set()
    for href in _PDF.findall(html or ""):
        url = urljoin(base, href)
        if url in seen:
            continue
        seen.add(url)
        name = _name(url)
        lowered = name.lower()
        kind = "hansard" if "hansard" in lowered else "order_paper" if "order paper" in lowered else None
        if not kind:
            continue
        dates = upcoming.dates_in(name.replace("_", " "), None)
        out.append({"url": url, "name": name, "kind": kind, "date": dates[0] if dates else None,
                    "supplementary": "supplementary" in lowered})
    return out


def pdf_text(url: str) -> str:
    response = http.get(url, timeout=90)
    response.raise_for_status()
    if len(response.content) > MAX_PDF_BYTES:
        raise ValueError(f"{len(response.content)} bytes, over the {MAX_PDF_BYTES} ceiling")
    from pypdf import PdfReader

    return "\n".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(response.content)).pages)


def turns(text: str) -> list[tuple[str, str]]:
    """(speaker as printed, what they said) for every turn in a Hansard."""
    marks = list(_SPEAKER.finditer(text or ""))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        said = " ".join(text[m.end():end].split())
        if len(said) > 40:
            out.append((" ".join(m.group(1).split()), said))
    return out


def agenda_items(text: str) -> list[str]:
    """An order paper's items: its numbered lines, joined across the PDF's
    line breaks."""
    items = re.split(r"\n\s*(?=\d{1,2}\.\s|\(\w\)\s)", text or "")
    return [" ".join(i.split()) for i in items if len(" ".join(i.split())) > 25]


def fetch(about, ws: datetime, we: datetime, report: dict | None = None) -> tuple[list[IngestedMention], list[dict]]:
    """(speeches on the issue, sittings ahead on the issue). `about(text)` is
    the map's own test of whether a passage is about the issue."""
    report = report if report is not None else {}
    docs: dict[str, dict] = {}
    for listing in LISTINGS:
        try:
            response = http.get(listing, timeout=30)
            response.raise_for_status()
            for doc in documents(response.text, listing):
                docs.setdefault(doc["url"], doc)
        except Exception as exc:  # noqa: BLE001 — one listing down is said, not fatal
            report.setdefault("errors", []).append(f"{listing}: {type(exc).__name__}: {exc}"[:200])
    today = datetime.utcnow().date()
    hansards = sorted([d for d in docs.values() if d["kind"] == "hansard" and d["date"]
                       and ws.date() <= d["date"] <= we.date()], key=lambda d: d["date"], reverse=True)
    papers = sorted([d for d in docs.values() if d["kind"] == "order_paper" and d["date"]
                     and d["date"] >= today], key=lambda d: d["date"])
    report.update({"documents_listed": len(docs), "hansards_in_window": len(hansards),
                   "order_papers_ahead": len(papers)})

    mentions: list[IngestedMention] = []
    for doc in hansards[:MAX_HANSARDS]:
        try:
            text = pdf_text(doc["url"])
        except Exception as exc:  # noqa: BLE001
            report.setdefault("errors", []).append(f"{doc['name']}: {type(exc).__name__}: {exc}"[:200])
            continue
        report["hansards_read"] = report.get("hansards_read", 0) + 1
        kept = 0
        for speaker, said in turns(text):
            if not about(said):
                continue
            mentions.append(IngestedMention(
                platform="parliament.go.ke", source_type="hansard", author_handle=speaker,
                text=said[:MAX_TURN_CHARS],
                posted_at=datetime.combine(doc["date"], datetime.min.time()), engagement={},
                raw_payload={"url": doc["url"], "record": "primary", "source": "hansard",
                             "sitting": doc["date"].isoformat(), "document": doc["name"],
                             "date_estimated": False}))
            kept += 1
            if kept >= MAX_TURNS_PER_SITTING:
                break

    ahead: list[dict] = []
    for doc in papers[:MAX_ORDER_PAPERS]:
        try:
            text = pdf_text(doc["url"])
        except Exception as exc:  # noqa: BLE001
            report.setdefault("errors", []).append(f"{doc['name']}: {type(exc).__name__}: {exc}"[:200])
            continue
        report["order_papers_read"] = report.get("order_papers_read", 0) + 1
        for item in agenda_items(text):
            if about(item):
                ahead.append({"date": doc["date"].isoformat(), "event": item[:400],
                              "publisher": "Parliament of Kenya — Order Paper", "url": doc["url"],
                              "printed": None, "basis": "stated_in_source", "record": "order_paper"})
    report.update({"speeches_on_issue": len(mentions), "sittings_ahead_on_issue": len(ahead)})
    return mentions, ahead

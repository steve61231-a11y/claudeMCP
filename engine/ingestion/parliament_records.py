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

What it returns is the source's own words: a speech is the part of the
speaker's turn on the issue (the whole turn when the business it was made
under names the issue), under the speaker's name as printed, dated by the
sitting, with that business kept beside it; an agenda item is the order
paper's line. Page furniture and headings are the Hansard's, not the
member's, and are never quoted as theirs. Nothing is summarised here.
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
MAX_PDF_BYTES = 15_000_000

_PDF = re.compile(r'href="([^"]+\.pdf)"', re.I)
#: A speaker's label at the start of a turn, as the Hansard prints it: a
#: title, a NAME (capitalised words only — an honorific in brackets
#: allowed), an optional constituency and party in brackets, a colon. The
#: first pattern ran to any colon on the line, and a live run attributed a
#: speech to "Hon. Temporary Speaker, I wish to be remembered for one thin".
_NAME = r"(?:\((?:Dr|Prof|Eng|Amb|Capt|Gen|Rtd|CPA|Ms|Mrs|Mr|Arch|Maj)\.?\)\s+)?(?:[A-Z][\w’'.-]*\s+){0,5}[A-Z][\w’'.-]*"
_SPEAKER = re.compile(
    rf"(?:^|\n)[ \t]*((?:Hon\.|Sen\.)\s+{_NAME}(?:\s*\([^()\n]{{2,90}}\))?"
    rf"|The (?:Temporary |Deputy )?Speaker(?:\s*\([^()\n]{{2,90}}\))?"
    rf"|The (?:Temporary )?Chairperson(?:\s*\([^()\n]{{2,90}}\))?)\s*:\s", re.M)
MAX_PASSAGE_CHARS = 2500


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


#: Page furniture as pypdf extracts it (scripts/record_probe.py --layout,
#: 2 Nov 2026): every page opens "22nd October 2026  National Assembly
#: Debates  1" and a two-line disclaimer. Left in, the disclaimer was kept
#: as the sentence before a member's remarks on the housing levy.
_PAGE_HEADER = re.compile(r"^[^\n]*\b(?:National Assembly|Senate) Debates[ \t]+\d+[ \t]*$", re.M | re.I)
_DISCLAIMER = re.compile(r"\s+".join(
    "Disclaimer: The electronic version of the Official Hansard Report is for information purposes only. "
    "A certified copy of this Report can be obtained from the Hansard Editor.".split()
).replace(r"purposes\s+only", r"purposes\s*only").replace(".", r"\."), re.I)


def clean(text: str) -> str:
    """The Hansard without its page furniture."""
    return _PAGE_HEADER.sub("", _DISCLAIMER.sub(" ", text or ""))


_JOIN_END = frozenset("THE OF TO FOR AND IN ON BY & A AN".split())
#: A line that opens with one of these finishes the heading above it — a
#: Bill's title wrapped before "BILL" or "(AMENDMENT)".
_JOIN_START = frozenset("FOR TO OF AND ON BY & BILL BILL, ACT REGULATIONS RULES (AMENDMENT) AMENDMENT".split())


def _is_heading(line: str) -> bool:
    """An order-of-business heading: a line in capitals, made of words
    ("PETITIONS", "DISPARITIES IN TEA PRICING…"), not a reference number
    quoted in a speech ("NEPAD/KFW 2015 69 052/FW1/KEN/005")."""
    stripped = line.strip()
    # A heading never ends a sentence: "IEBC." or "208(8)." alone on a
    # line is the tail of a speech the PDF wrapped.
    if not stripped or re.search(r"[a-z]", stripped) or stripped[-1] in ".,;:":
        return False
    letters = sum(c.isalpha() for c in stripped)
    visible = sum(not c.isspace() for c in stripped)
    return bool(re.search(r"[A-Z]{3,}", stripped)) and letters >= 4 and letters / max(visible, 1) >= 0.7


def turns(text: str) -> list[tuple[str, str, str | None]]:
    """(speaker as printed, what they said, the business it was said
    under) for every turn in a Hansard. A heading is the Hansard's, not
    the member's: it is taken out of the speech and becomes the business
    for what follows — "PUBLIC DISCLOSURE OF KENYA'S GOVERNANCE AND
    CORRUPTION DIAGNOSTIC REPORT BY IMF" was printed inside the Speaker's
    remarks."""
    text = clean(text)
    marks = list(_SPEAKER.finditer(text))
    business: str | None = None

    def walk(segment: str, speaker: str | None, out: list) -> None:
        nonlocal business
        lines, pending_heading = [], []
        for line in segment.split("\n"):
            if _is_heading(line):
                if lines and speaker:
                    said = " ".join(" ".join(lines).split())
                    if len(said) > 40:
                        out.append((speaker, said, business))
                    lines = []
                line = " ".join(line.split())
                # One heading over two lines ("DELEGATION FROM THE" /
                # "PARLIAMENT OF MALAWI") is joined; two headings in a row
                # ("PRAYERS" / "COMMUNICATIONS FROM THE CHAIR") are not.
                if pending_heading and (pending_heading[-1].split()[-1] in _JOIN_END
                                        or line.split()[0] in _JOIN_START or line.startswith("(")):
                    pending_heading[-1] += " " + line
                else:
                    pending_heading.append(line)
                continue
            if pending_heading:
                business = pending_heading[-1]
                pending_heading = []
            lines.append(line)
        if pending_heading:
            business = pending_heading[-1]
        said = " ".join(" ".join(lines).split())
        if speaker and len(said) > 40:
            out.append((speaker, said, business))

    out: list[tuple[str, str, str | None]] = []
    walk(text[:marks[0].start()] if marks else text, None, out)
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        walk(text[m.end():end], " ".join(m.group(1).split()), out)
    return out


def passage(said: str, about) -> str | None:
    """The part of a speech that is about the issue: each sentence that
    names it with the sentence either side, in order, as printed. A turn
    of twenty minutes that mentions the issue once is not a speech about
    it, and keeping it whole put a member's remarks on Standing Orders
    under an SHA map."""
    sentences = re.split(r"(?<=[.!?])\s+", said)
    hits = [i for i, sentence in enumerate(sentences) if about(sentence)]
    if not hits:
        return None
    keep = sorted({j for i in hits for j in (i - 1, i, i + 1) if 0 <= j < len(sentences)})
    out, last = [], None
    for j in keep:
        if last is not None and j != last + 1:
            out.append("[…]")
        out.append(sentences[j])
        last = j
    return " ".join(out)[:MAX_PASSAGE_CHARS]


def opening(said: str) -> str:
    """A whole contribution, to the ceiling, cut at a sentence."""
    if len(said) <= MAX_PASSAGE_CHARS:
        return said
    cut = said[:MAX_PASSAGE_CHARS]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > MAX_PASSAGE_CHARS // 2 else cut) + " […]"


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
        for speaker, said, business in turns(text):
            # Under a heading that names the issue, the whole contribution
            # is on it — a member debating THE AFFORDABLE HOUSING BILL says
            # "this Bill", not its name. Otherwise only what names it.
            words = (opening(said) if business and about(business) else None) or passage(said, about)
            if not words:
                continue
            mentions.append(IngestedMention(
                platform="parliament.go.ke", source_type="hansard", author_handle=speaker,
                text=words,
                posted_at=datetime.combine(doc["date"], datetime.min.time()), engagement={},
                raw_payload={"url": doc["url"], "record": "primary", "source": "hansard",
                             "sitting": doc["date"].isoformat(), "document": doc["name"],
                             "business": business, "date_estimated": False}))
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

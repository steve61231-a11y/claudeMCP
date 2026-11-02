"""The World Bank's record on Kenya, read for the Issue Map.

An issue of public money, energy, transport or international finance — the
IMF programme, a road, a power line, a health system loan — usually has a
lender's record beside the Kenyan one: the project, its amount, its dates,
the documents that set its conditions. The World Bank publishes both as a
free, keyless JSON API, reachable from the live server
(`evidence/record_reach.py`). Observed contract (scripts/source_probe.py,
2 Nov 2026):

    GET https://search.worldbank.org/api/v2/projects?format=json&countrycode_exact=KE&qterm=…
    -> {"rows", "total", "projects": {"P123456": {...}, ...}}
    GET https://search.worldbank.org/api/v2/wds?format=json&qterm=Kenya …
    -> {"rows", "total", "documents": {"D123": {...}, ..., "facets": {...}}}

Fields are read defensively — a key the API does not send is left out, never
filled — and each item is the lender's own words (project name, document
title), dated by its own date, linked to its own page.
"""

from __future__ import annotations

from datetime import datetime

from engine.ingestion import http
from engine.ingestion.base import IngestedMention

PROJECTS = "https://search.worldbank.org/api/v2/projects"
DOCUMENTS = "https://search.worldbank.org/api/v2/wds"
ROWS = 5


def _date(value) -> datetime | None:
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def projects(term: str) -> list[IngestedMention]:
    response = http.get(PROJECTS, params={"format": "json", "countrycode_exact": "KE", "qterm": term,
                                          "rows": ROWS}, timeout=30)
    response.raise_for_status()
    out = []
    for pid, p in ((response.json() or {}).get("projects") or {}).items():
        if not isinstance(p, dict) or not p.get("project_name"):
            continue
        amount = p.get("totalamt") or p.get("totalcommamt")
        bits = [f"World Bank project {pid}: {' '.join(str(p['project_name']).split())}"]
        if amount:
            bits.append(f"commitment US${amount}")
        if p.get("status"):
            bits.append(f"status {p['status']}")
        when = _date(p.get("boardapprovaldate"))
        # Who borrows and who carries the project out, as the Bank records
        # them — the roles a connection path can rest on. Absent, absent.
        borrower = " ".join(str(p.get("borrower") or "").split()) or None
        agency = " ".join(str(p.get("impagency") or "").split()) or None
        if agency:
            bits.append(f"implementing agency {agency}")
        if borrower:
            bits.append(f"borrower {borrower}")
        out.append(IngestedMention(
            platform="worldbank.org", source_type="report", author_handle="World Bank",
            text=". ".join(bits), posted_at=when or datetime.utcnow(), engagement={},
            raw_payload={"url": p.get("url") or f"https://projects.worldbank.org/en/projects-operations/project-detail/{pid}",
                         "record": "international", "source": "world_bank_projects", "project_id": pid,
                         "project": " ".join(str(p["project_name"]).split()),
                         "borrower": borrower, "implementing_agency": agency,
                         "date_estimated": when is None}))
    return out


def documents(term: str) -> list[IngestedMention]:
    response = http.get(DOCUMENTS, params={"format": "json", "qterm": f"Kenya {term}", "rows": ROWS},
                        timeout=30)
    response.raise_for_status()
    out = []
    for did, d in ((response.json() or {}).get("documents") or {}).items():
        if not isinstance(d, dict) or did == "facets" or not d.get("display_title"):
            continue
        when = _date(d.get("docdt"))
        out.append(IngestedMention(
            platform="worldbank.org", source_type="report", author_handle="World Bank",
            # Titles arrive with the API's own line breaks and indentation.
            text=" ".join(" ".join(str(x) for x in (d.get("docty"), "—", d["display_title"]) if x).split()),
            posted_at=when or datetime.utcnow(), engagement={},
            raw_payload={"url": d.get("url") or d.get("pdfurl"), "record": "international",
                         "source": "world_bank_documents", "date_estimated": when is None}))
    return out

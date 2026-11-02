"""Run the Issue Map's record reading for real, on issues of several kinds.

Unit tests hold each connector to the contract the source probe observed;
this runs `issue_map.acquire_records` — the connectors AND the map's own
filtering — against the live sources from GitHub's network, so a change is
never declared working on a fake alone. The issues and principals are
public topics and public institutions, never anyone a client is researching.

    PYTHONPATH=. python scripts/record_probe.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from engine.reports import issue_map

CASES = [
    ("Ministry of Health", "SHA"),
    ("Ministry of Education", "school fees"),
    ("National Treasury", "Housing levy"),
    ("Kenya Forest Service", "Mau forest evictions"),
    ("National Treasury", "the IMF loan"),
    ("Kenya Government", "Safaricom shares sale"),
    ("Ministry of Health", "Social Health Authority"),
    ("National Treasury", "Finance Bill"),
]


def main() -> int:
    we = datetime.utcnow()
    ws = we - timedelta(days=90)
    for principal, issue in CASES:
        report: dict = {}
        mentions, ahead = issue_map.acquire_records(principal, issue, None, ws, we,
                                                    issue_map.principal_variants(principal), report)
        print(f"\n=== {principal} × {issue} · kinds={report.get('kinds')} · total={report.get('total')}",
              file=sys.stderr)
        for source in ("kenya_law", "parliament", "world_bank", "gazette"):
            print(f"{source.upper()}: {report.get(source)}", file=sys.stderr)
        by_kind: dict = {}
        for m in mentions:
            by_kind.setdefault(m["source_type"], []).append(m)
        for kind, items in by_kind.items():
            for m in items[:3]:
                print(f"   - [{kind}] {m['posted_at'].date()} {m['author_handle'][:50]}: {m['text'][:200]}",
                      file=sys.stderr)
        for a in ahead[:3]:
            print(f"   - AHEAD {a['date']}: {a['event'][:160]}", file=sys.stderr)
        from engine.evidence import paths

        found = paths.build(mentions, principal, issue_map.principal_variants(principal), issue,
                            ahead=ahead, sources=report)
        print(f"   PATHS: {found.get('lead') or found.get('reason')}", file=sys.stderr)
        for p in found.get("paths", [])[:4]:
            s0 = p["steps"][0]
            print(f"     · {s0['from']} — {s0['relation']} → {s0['to']} | as printed: {s0.get('as_printed')!r}"
                  f" | via {s0.get('basis')} | {s0['record'].get('date')}", file=sys.stderr)
            for listing in p.get("listings") or []:
                print(f"         listed {listing['date']}: {listing.get('snippet', '')[:160]!r}", file=sys.stderr)
        # Every case's cause-list lookup, matched or not — so "nothing ahead"
        # can be told from "the matcher found nothing at all".
        from engine.ingestion import kenya_law_records

        for m in mentions:
            raw = m.get("raw_payload") or {}
            if raw.get("source") == "kenya_law_search" and raw.get("case_number"):
                found_dates, why = kenya_law_records.listings(raw["case_number"], raw.get("court_code"),
                                                              raw.get("parties") or [])
                print(f"     LISTINGS {raw['case_number'][:40]} ({raw.get('court_code')}, party "
                      f"{kenya_law_records._first_party_word(raw.get('parties') or [])}): "
                      f"{[x['date'] for x in found_dates]} {why or ''}", file=sys.stderr)
        _gazette_passages(issue)
    _lender_fields()
    return 0


def _gazette_passages(issue: str) -> None:
    """How often the Gazette reader COULD fire: passages naming the issue,
    and of those, passages with deadline words — past ones included, so a
    method that finds nothing ahead today can be told from one that never
    finds anything."""
    from engine.evidence import gazette_deadlines as gd
    from engine.ingestion import kenya_law_records
    from engine.reports import issue_match, issue_profile

    about = issue_match.of(issue)
    since = (datetime.utcnow() - timedelta(days=365)).date().isoformat()
    naming = worded = 0
    for term in issue_profile.profile(issue, None)["search_terms"][:2]:
        cards, count, why = kenya_law_records.search(term, params={"nature": "Gazette", "ordering": "-date",
                                                                   "date__gte": since})
        print(f"   GAZETTE “{term}”: {count} issues since {since}, {len(cards)} read {why or ''}", file=sys.stderr)
        for card in cards:
            for passage in gd.fragments(card.get("card_text") or ""):
                if not about(passage):
                    continue
                naming += 1
                if gd.STATED.search(passage) or gd.RELATIVE.search(passage):
                    worded += 1
                    print(f"     deadline words [{card['url'][-24:]}]: {passage[:220]!r}", file=sys.stderr)
    print(f"   GAZETTE passages naming the issue: {naming}; with deadline words: {worded}", file=sys.stderr)


def _lender_fields() -> None:
    """The World Bank project fields as the API sends them, so the roles a
    path rests on are read under the keys that exist."""
    from engine.ingestion import http, world_bank_records

    response = http.get(world_bank_records.PROJECTS, params={"format": "json", "countrycode_exact": "KE",
                                                              "qterm": "health", "rows": 2}, timeout=30)
    for pid, p in list(((response.json() or {}).get("projects") or {}).items())[:2]:
        print(f"\n=== WB {pid} keys: {sorted(p)[:60]}", file=sys.stderr)
        print(f"   borrower={p.get('borrower')!r} impagency={p.get('impagency')!r}", file=sys.stderr)


if __name__ == "__main__" and "--layout" not in sys.argv:
    raise SystemExit(main())


def layout() -> None:
    """The page furniture of one Hansard as pypdf extracts it — headers,
    footers, headings — so the cleanup is written against what is printed."""
    import re

    from engine.ingestion import http, parliament_records

    response = http.get(parliament_records.LISTINGS[0], timeout=30)
    docs = [d for d in parliament_records.documents(response.text, parliament_records.LISTINGS[0])
            if d["kind"] == "hansard" and d["date"]]
    doc = sorted(docs, key=lambda d: d["date"], reverse=True)[0]
    text = parliament_records.pdf_text(doc["url"])
    lines = text.split("\n")
    print(f"\n=== LAYOUT {doc['name']} · {len(lines)} lines", file=sys.stderr)
    for line in lines[:40]:
        print(f"   | {line[:160]!r}", file=sys.stderr)
    for needle in ("certified copy", "DEBATES", "Disclaimer"):
        for m in list(re.finditer(re.escape(needle), text))[:3]:
            print(f"   [{needle}] {text[max(0, m.start() - 220):m.end() + 160]!r}", file=sys.stderr)
    caps = [line.strip() for line in lines if line.strip() and not re.search(r"[a-z]", line)
            and len(line.strip()) > 6]
    print(f"   all-caps lines: {len(caps)}", file=sys.stderr)
    for line in caps[:30]:
        print(f"   ^ {line[:160]!r}", file=sys.stderr)


if __name__ == "__main__" and "--layout" in sys.argv:
    layout()

"""Run the Issue Map's record connectors for real, on issues of several kinds.

Unit tests hold each connector to the contract the source probe observed;
this runs them against the live sources from GitHub's network, so a
connector is never declared working on a fake alone. The issues and
institutions are public topics, never anyone a client is researching.

    PYTHONPATH=. python scripts/record_probe.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta

from engine.ingestion import kenya_law_records, parliament_records, world_bank_records
from engine.reports import issue_profile, relevance

ISSUES = ["SHA", "school fees", "Housing levy", "Mau forest evictions", "the IMF loan", "Safaricom shares sale"]


def main() -> int:
    we = datetime.utcnow()
    ws = we - timedelta(days=90)
    listing_cache: dict = {}
    for issue in ISSUES:
        p = issue_profile.profile(issue)
        terms = p["search_terms"]
        match = relevance.merge_terms(terms, p["identities"])
        print(f"\n=== {issue} · kinds={p['kinds']} · terms={terms[:4]}", file=sys.stderr)
        for query in terms[:2]:
            records, count, why = kenya_law_records.search(query)
            print(f"KENYA LAW “{query}”: {len(records)} parsed of {count} · why={why}", file=sys.stderr)
            for r in records[:2]:
                print(f"   - [{r['kind']}] {r['date'] and r['date'].date()} {r['court']} · {r['title'][:110]} "
                      f"· parties={r['parties'][:2]} · case={r['case_number']}", file=sys.stderr)
        report: dict = {}
        speeches, ahead = parliament_records.fetch(lambda t: relevance.mentions_issue(t, match), ws, we, report)
        print(f"PARLIAMENT: {report}", file=sys.stderr)
        for s in speeches[:2]:
            print(f"   - {s['posted_at'].date()} {s['author_handle'][:60]}: {s['text'][:160]}", file=sys.stderr)
        for a in ahead[:2]:
            print(f"   - AHEAD {a['date']}: {a['event'][:160]}", file=sys.stderr)
        if "world_bank" in p["records"]:
            try:
                wb = world_bank_records.projects(terms[0]) + world_bank_records.documents(terms[0])
                print(f"WORLD BANK: {len(wb)} items", file=sys.stderr)
                for w in wb[:3]:
                    print(f"   - {w['posted_at'].date()} {w['text'][:140]}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"WORLD BANK error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

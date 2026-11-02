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
        for source in ("kenya_law", "parliament", "world_bank"):
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the evidence pipeline over data already in the database, and print it.

Deliberately standalone. It does not touch the report pipeline, does not write
to the database, and changes nothing a client sees — the point is to look at
what this architecture produces on the corpus we already have BEFORE anything is
built on top of it.

    python -m engine.evidence.slice "Edwin Sifuna" --limit 120 --out slice.json
    python -m engine.evidence.slice "Edwin Sifuna" --no-review   # skip LLM review
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

from engine.evidence import findings as findings_mod
from engine.evidence import records as records_mod
from engine.evidence.independence import independence


def load_corpus(subject: str, days: int, limit: int | None) -> list[dict]:
    """Read stored mentions for a subject. Same shape the report pipeline uses."""
    from engine.db.models import Politician, RawMention
    from engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        politician = (
            session.query(Politician)
            .filter(Politician.name.ilike(f"%{subject}%"))
            .order_by(Politician.name)
            .first()
        )
        if politician is None:
            raise SystemExit(f"No subject in the database matching {subject!r}.")
        since = datetime.utcnow() - timedelta(days=days)
        query = (
            session.query(RawMention)
            .filter(RawMention.politician_id == politician.id)
            .filter(RawMention.posted_at >= since)
            .order_by(RawMention.posted_at.desc())
        )
        rows = query.limit(limit).all() if limit else query.all()
        return [
            {"id": r.id, "platform": r.platform, "source_type": r.source_type,
             "author_handle": r.author_handle, "text": r.text, "posted_at": r.posted_at,
             "engagement": r.engagement_json or {}, "source_url": r.source_url}
            for r in rows
        ]
    finally:
        session.close()


def run(subject: str, mentions: list[dict], review: bool = True,
        top_n: int = 12) -> dict:
    """Corpus in, structured intelligence out. No prose is generated here."""
    independence_stats = independence(mentions)
    records = records_mod.extract_records(subject, mentions)
    built = findings_mod.build_findings(records, mentions, top_n=top_n, review=review)
    return {
        "subject": subject,
        "generated_at": datetime.utcnow().isoformat(),
        "independence": independence_stats,
        "extraction": records_mod.coverage(records, mentions),
        "findings": [f.to_dict() for f in built],
        "rejected_by_review": [f.title for f in findings_mod.unsupported(built)],
    }


def summarise(result: dict) -> str:
    """A terminal read of the slice — enough to judge it without opening JSON."""
    ind, ext = result["independence"], result["extraction"]
    lines = [
        f"SUBJECT   {result['subject']}",
        "",
        "CORPUS",
        f"  {ind['mentions']} mentions -> {ind['distinct_stories']} distinct stories "
        f"(amplification {ind['amplification']}x)",
        f"  {ind['distinct_platforms']} platforms, {ind['distinct_authors']} authors, "
        f"largest duplicate group {ind['largest_group']}",
        "",
        "EVIDENCE",
        f"  {ext['records']} records from {ext['mentions_yielding_evidence']}"
        f"/{ext['mentions_read']} mentions",
        f"  by kind   {ext['by_kind']}",
        f"  by status {ext['by_status']}",
        "",
        f"FINDINGS ({len(result['findings'])})",
    ]
    for finding in result["findings"]:
        verdict = (finding.get("review") or {}).get("verdict", "—")
        lines += [
            "",
            f"  ▸ {finding['title']}   [{finding['confidence']}] {verdict}",
            f"    {finding['summary']}",
            f"    {finding['mention_count']} mentions / "
            f"{finding['independent_sources']} independent stories / "
            f"{finding['distinct_platforms']} platforms · {finding['trend']}"
            f" ({finding['trend_detail']})",
            f"    why: {finding['confidence_reason']}",
        ]
        if finding.get("contradicting"):
            lines.append(f"    CONTRADICTED by {len(finding['contradicting'])} record(s):")
            for row in finding["contradicting"][:2]:
                lines.append(f"      - {row['statement'][:120]}")
        if finding.get("open_questions"):
            for question in finding["open_questions"]:
                lines.append(f"    open: {question}")
        if (finding.get("review") or {}).get("reason"):
            lines.append(f"    sceptic: {finding['review']['reason']}")
        for row in finding["supporting"][:2]:
            lines.append(f"      · [{row['status']}] {row['statement'][:110]}")
            lines.append(f"        {row.get('url') or 'no link captured'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--limit", type=int, default=150,
                        help="cap the corpus so a first look is cheap")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--no-review", action="store_true",
                        help="skip contradiction + sceptic passes (no LLM review calls)")
    parser.add_argument("--out", help="write the full structured result here")
    args = parser.parse_args(argv)

    mentions = load_corpus(args.subject, args.days, args.limit)
    if not mentions:
        print(f"No stored mentions for {args.subject!r} in the last {args.days} days.")
        return 1

    result = run(args.subject, mentions, review=not args.no_review, top_n=args.top)
    print(summarise(result))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(result, handle, indent=2, default=str)
        print(f"\nfull structured output -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

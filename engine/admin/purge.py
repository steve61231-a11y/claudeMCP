"""Delete accumulated run data, so a subject starts from nothing.

The corpus is designed to compound: mentions, documents, entities and reports
persist so a second run on the same subject is richer than the first. That is
right for production and wrong while the pipeline is being rebuilt underneath
it, because every run then shows material collected by a version of the system
that no longer exists — and the page renders the last stored payload for a
subject BEFORE the new run has produced anything, so old output appears
instantly and looks like new output.

This is irreversible. Nothing here runs without an explicit confirmation
phrase, and there is no default that deletes.

    python -m engine.admin.purge --all --confirm DELETE-EVERYTHING
    python -m engine.admin.purge --subject "Edwin Sifuna" --confirm DELETE-EVERYTHING
    python -m engine.admin.purge --all --dry-run
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text
from psycopg2 import errors
from sqlalchemy.exc import ProgrammingError

CONFIRMATION = "DELETE-EVERYTHING"

#: Everything a run writes, in an order that respects foreign keys when deleted
#: one table at a time. TRUNCATE handles the ordering itself, but the count
#: pass reads them in this order too and it reads better bottom-up.
RUN_DATA_TABLES = [
    "claim_evidence",
    "claims",
    "event_evidence",
    "events",
    "entity_relationships",
    "mention_classifications",
    "mention_narratives",
    "narrative_metrics",
    "narratives",
    "mention_sentiment",
    "mention_entities",
    "entities",
    "raw_mentions",
    "documents",
    "author_profiles",
    "source_credibility",
    "ingestion_tasks",
    "ingestion_runs",
    "intelligence_reports",
    "alerts",
    "snapshots",
    "run_progress",
    "politicians",
]

#: Deliberately NOT purged by default. `llm_usage` is the record of what was
#: spent, which is accounting rather than report data — losing it to a corpus
#: reset would destroy the only history of what the runs cost.
PRESERVED_BY_DEFAULT = ["llm_usage"]

#: Never touched. Dropping this makes the database unmigratable.
NEVER_TOUCH = ["alembic_version"]


def counts(session, tables: list[str] | None = None) -> dict[str, int]:
    """Row counts per table, so a purge can be seen before and after."""
    out: dict[str, int] = {}
    for table in tables or RUN_DATA_TABLES:
        try:
            out[table] = int(session.execute(
                text(f"SELECT count(*) FROM {table}")).scalar() or 0)
        except Exception:  # noqa: BLE001 — a table the schema no longer has
            continue
    return out


def purge_all(session, confirm: str) -> dict:
    """Empty every table a run writes to. Irreversible.

    TRUNCATE in a single statement rather than table-by-table DELETE: it
    handles the foreign keys between these tables itself, and on a corpus of
    hundreds of thousands of rows it is the difference between seconds and
    minutes."""
    if confirm != CONFIRMATION:
        raise ValueError(
            f"refusing to purge without confirm={CONFIRMATION!r}; this cannot be undone")

    before = counts(session)
    present = [t for t in RUN_DATA_TABLES if t in before]
    if present:
        session.execute(text(
            f"TRUNCATE TABLE {', '.join(present)} RESTART IDENTITY CASCADE"))
        session.commit()
    after = counts(session)
    return {
        "purged": "all",
        "tables": present,
        "rows_deleted": {t: before.get(t, 0) for t in present if before.get(t, 0)},
        "total_rows_deleted": sum(before.get(t, 0) for t in present),
        "remaining": {t: n for t, n in after.items() if n},
        "preserved": PRESERVED_BY_DEFAULT,
    }


def purge_subject(session, name: str, confirm: str) -> dict:
    """Delete one subject and everything collected under them.

    Rows keyed by politician_id go with the subject. Rows that are not — the
    shared entity graph, source credibility — are left alone, because they are
    not this subject's to delete."""
    if confirm != CONFIRMATION:
        raise ValueError(
            f"refusing to purge without confirm={CONFIRMATION!r}; this cannot be undone")

    from engine.db.models import Politician

    subjects = (session.query(Politician)
                .filter(Politician.name.ilike(f"%{name}%")).all())
    if not subjects:
        return {"purged": "subject", "matched": [], "note": f"no subject matching {name!r}"}

    ids = [s.id for s in subjects]
    names = [s.name for s in subjects]
    deleted: dict[str, int] = {}

    # Children first, then the mentions and documents they hang off, then the
    # subject. Each statement names its parent explicitly rather than relying
    # on cascade, so a missing FK constraint cannot leave orphans behind.
    statements = [
        ("mention_classifications", "mention_id IN (SELECT id FROM raw_mentions WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("mention_sentiment", "mention_id IN (SELECT id FROM raw_mentions WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("mention_entities", "mention_id IN (SELECT id FROM raw_mentions WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("mention_narratives", "mention_id IN (SELECT id FROM raw_mentions WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("narrative_metrics", "narrative_id IN (SELECT id FROM narratives WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("narratives", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("event_evidence", "event_id IN (SELECT id FROM events WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("events", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("claim_evidence", "claim_id IN (SELECT id FROM claims WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("claims", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("raw_mentions", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("documents", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("ingestion_tasks", "run_id IN (SELECT id FROM ingestion_runs WHERE politician_id = ANY(CAST(:ids AS uuid[])))"),
        ("ingestion_runs", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("intelligence_reports", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("alerts", "politician_id = ANY(CAST(:ids AS uuid[]))"),
        ("politicians", "id = ANY(CAST(:ids AS uuid[]))"),
    ]
    skipped: dict[str, str] = {}
    for table, where in statements:
        try:
            result = session.execute(text(f"DELETE FROM {table} WHERE {where}"), {"ids": ids})
            if result.rowcount:
                deleted[table] = int(result.rowcount)
        except ProgrammingError as exc:
            # Skip a table this schema genuinely does not have. Nothing else:
            # a purge that silently deletes nothing and reports success is
            # worse than one that fails, because the caller then runs again on
            # data they believe is already gone. Matching on "does not exist"
            # was too loose — it also matched "operator does not exist: uuid =
            # text", which is a fault in the QUERY, and swallowing that turned
            # a broken purge into a confident no-op.
            session.rollback()
            if not isinstance(getattr(exc, "orig", None), errors.UndefinedTable):
                raise
            skipped[table] = "table not present in this schema"

    # The cached progress payload is keyed by subject name, not id, and it is
    # the one the page renders BEFORE a new run produces anything — leaving it
    # behind is exactly what makes old output look like new output.
    for subject_name in names:
        session.execute(text("DELETE FROM run_progress WHERE subject_key ILIKE :k"),
                        {"k": f"%{subject_name.lower()}%"})
    session.commit()
    return {"purged": "subject", "matched": names, "rows_deleted": deleted,
            "total_rows_deleted": sum(deleted.values()), "skipped": skipped}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="delete EVERY subject's data")
    group.add_argument("--subject", help="delete one subject and their corpus")
    parser.add_argument("--confirm", default="",
                        help=f"must be {CONFIRMATION!r}; nothing is deleted without it")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what is there, delete nothing")
    args = parser.parse_args(argv)

    from engine.db.session import SessionLocal

    session = SessionLocal()
    try:
        if args.dry_run:
            present = {t: n for t, n in counts(session).items() if n}
            total = sum(present.values())
            print(f"{total} rows across {len(present)} tables would be deleted:")
            for table, n in sorted(present.items(), key=lambda kv: -kv[1]):
                print(f"  {n:>9,}  {table}")
            print(f"\nPreserved: {', '.join(PRESERVED_BY_DEFAULT)}")
            print(f"Run again with --confirm {CONFIRMATION} to delete.")
            return 0

        result = (purge_all(session, args.confirm) if args.all
                  else purge_subject(session, args.subject, args.confirm))
        print(f"deleted {result.get('total_rows_deleted', 0):,} rows")
        for table, n in sorted((result.get("rows_deleted") or {}).items(),
                               key=lambda kv: -kv[1]):
            print(f"  {n:>9,}  {table}")
        return 0
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())

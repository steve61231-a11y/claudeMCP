"""dates moved into a window are not dates

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-10-21

Every collector used to clamp an item's date into the window it was
collected for — `min(max(posted, window_start), window_end)` — so a 2022
election article was stored as published on the first day of the month a
report happened to cover, and an investor found "mentions going back to the
last election" inside a one-month report. Measured on 21 Oct 2026: 47% of
Google News and 35% of YouTube results for a one-month query predated the
month. The collectors no longer move a date; this repairs what they stored.

A clamped date is recognisable exactly: it EQUALS the window start or the
window end of the run that collected it, to the microsecond, because it was
copied from there. The real date is not recoverable from the row, so the row
is marked `date_estimated` — kept in the store, and left out of every
period's analysis by `engine.db.period.dated()` — rather than given a date
nobody knows. Undated items stamped with the window end were already marked;
YouTube comments, which inherited their video's stamped date, were not.

Downgrade leaves the marks: they are a correction, and removing them would
put the old items back into every report.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Exposed so the repair itself can be tested against rows written AFTER the
#: test schema is built — the migration runs before any test data exists.
REPAIR_SQL = """
    UPDATE raw_mentions AS m
    SET raw_payload = COALESCE(m.raw_payload, '{}'::jsonb)
        || jsonb_build_object('date_estimated', true,
                              'date_repair', 'clamped to the collection window')
    FROM ingestion_runs AS r
    WHERE m.run_id = r.id
      AND (m.posted_at = r.window_start OR m.posted_at = r.window_end)
      AND COALESCE(m.raw_payload ->> 'date_estimated', 'false') <> 'true'
"""


def upgrade() -> None:
    op.execute(REPAIR_SQL)


def downgrade() -> None:
    pass

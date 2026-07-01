"""dedupe raw_mentions and enforce cross-run uniqueness

Revision ID: b1c2d3e4f5a6
Revises: 599ec097724c
Create Date: 2026-07-01

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = '599ec097724c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Re-running overlapping windows used to insert the same mention again;
    # keep the earliest copy of each duplicate (and its child rows) before
    # adding the uniqueness guarantee.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY politician_id, platform, content_hash
                ORDER BY created_at, id
            ) AS rn
            FROM raw_mentions
            WHERE content_hash IS NOT NULL
        ),
        dupes AS (SELECT id FROM ranked WHERE rn > 1)
        , _me AS (DELETE FROM mention_entities WHERE mention_id IN (SELECT id FROM dupes))
        , _ms AS (DELETE FROM mention_sentiment WHERE mention_id IN (SELECT id FROM dupes))
        , _mn AS (DELETE FROM mention_narratives WHERE mention_id IN (SELECT id FROM dupes))
        DELETE FROM raw_mentions WHERE id IN (SELECT id FROM dupes)
        """
    )
    op.create_unique_constraint(
        "uq_raw_mentions_politician_platform_hash",
        "raw_mentions",
        ["politician_id", "platform", "content_hash"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_raw_mentions_politician_platform_hash", "raw_mentions", type_="unique")

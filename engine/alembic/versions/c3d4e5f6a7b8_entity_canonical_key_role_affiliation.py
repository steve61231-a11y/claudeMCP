"""entity canonical_key, role, affiliation

Revision ID: c3d4e5f6a7b8
Revises: 2d67f5495269
Create Date: 2026-07-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = '2d67f5495269'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('entities', sa.Column('canonical_key', sa.String(), nullable=True))
    op.add_column('entities', sa.Column('role', sa.String(), nullable=True))
    op.add_column('entities', sa.Column('affiliation', sa.String(), nullable=True))
    # Merge duplicate (type, name) entities before backfilling the key:
    # repoint mention links at the surviving row, then drop the extras.
    op.execute(
        """
        WITH ranked AS (
            SELECT id, FIRST_VALUE(id) OVER (
                PARTITION BY type, lower(name) ORDER BY id
            ) AS keep_id
            FROM entities
        ),
        dupes AS (SELECT id, keep_id FROM ranked WHERE id <> keep_id),
        _repoint AS (
            UPDATE mention_entities me SET entity_id = d.keep_id
            FROM dupes d WHERE me.entity_id = d.id
        )
        DELETE FROM entities WHERE id IN (SELECT id FROM dupes)
        """
    )
    # Backfill so existing rows participate in cross-run dedup.
    op.execute("UPDATE entities SET canonical_key = type || ':' || lower(name) WHERE canonical_key IS NULL")
    op.create_unique_constraint('uq_entities_canonical_key', 'entities', ['canonical_key'])


def downgrade() -> None:
    op.drop_constraint('uq_entities_canonical_key', 'entities', type_='unique')
    op.drop_column('entities', 'affiliation')
    op.drop_column('entities', 'role')
    op.drop_column('entities', 'canonical_key')

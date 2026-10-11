"""which query fetched each mention

Revision ID: a2b3c4d5e6f7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-11

A mention already records the RUN that fetched it. It does not record the
TASK, and a task is the query — so there was no way to ask the one question
the learning loop needs answered: of the material this query brought back,
how much of it turned out to be about the subject at all?

Without this the loop can only learn novelty (`IngestionTask.result_count`,
new non-duplicate rows). A query returning fifty off-topic rows then
outscores one returning three on-topic ones, and the loop optimises for
noise while looking like it is working.

Nullable, because every row already stored has no task to point at, and
backfilling one would mean inventing provenance. `learning.recall` falls
back to the novelty signal for subjects whose history predates this.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "raw_mentions",
        sa.Column("task_id", sa.dialects.postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_raw_mentions_task", "raw_mentions", "ingestion_tasks",
        ["task_id"], ["id"], ondelete="SET NULL",
    )
    # The loop's query is "every mention this task fetched", so the index is
    # on task_id alone and is the one this exists for.
    op.create_index("ix_raw_mentions_task_id", "raw_mentions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_raw_mentions_task_id", table_name="raw_mentions")
    op.drop_constraint("fk_raw_mentions_task", "raw_mentions", type_="foreignkey")
    op.drop_column("raw_mentions", "task_id")

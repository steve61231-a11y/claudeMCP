"""llm_usage: the bulk tier's share of each day's tokens

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-11-02

Production may run two models at very different prices (DeepSeek V4 Pro
for the analysts, a Flash model for bulk scoring). A single token total
cannot be priced when the tiers differ, so the bulk tier's share is kept
beside it; the strong tier is the remainder.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("bulk_input_tokens", sa.Integer(), server_default="0", nullable=True))
    op.add_column("llm_usage", sa.Column("bulk_output_tokens", sa.Integer(), server_default="0", nullable=True))


def downgrade() -> None:
    op.drop_column("llm_usage", "bulk_output_tokens")
    op.drop_column("llm_usage", "bulk_input_tokens")

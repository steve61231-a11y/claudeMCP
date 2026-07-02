"""alerts table for early-warning events

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("politician_id", UUID(as_uuid=False), sa.ForeignKey("politicians.id"), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("headline", sa.String(), nullable=False),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("delivered", sa.Integer(), nullable=True),
    )
    op.create_index("ix_alerts_politician_id", "alerts", ["politician_id"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_index("ix_alerts_politician_id", table_name="alerts")
    op.drop_table("alerts")

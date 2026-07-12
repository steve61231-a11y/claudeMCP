"""multi-domain subject_type on politicians

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "politicians",
        sa.Column("subject_type", sa.String(), nullable=False, server_default="politician"),
    )


def downgrade() -> None:
    op.drop_column("politicians", "subject_type")

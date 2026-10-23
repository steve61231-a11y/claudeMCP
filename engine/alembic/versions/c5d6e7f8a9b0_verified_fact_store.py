"""the verified fact store

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-23

Phase 2 of docs/FOUNDATION.md: facts extracted once per source, each
anchored to a quote found verbatim in that source, and a record of every
source read so "read, nothing in it" is distinguishable from "not read".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, None] = "b4c5d6e7f8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "facts",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("politician_id", UUID(as_uuid=False), sa.ForeignKey("politicians.id"), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False),
        sa.Column("mention_id", UUID(as_uuid=False),
                  sa.ForeignKey("raw_mentions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("document_id", UUID(as_uuid=False),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("sentiment", sa.String(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("statement_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("source_key", "statement_hash", name="uq_fact_source_statement"),
    )
    op.create_index("ix_facts_politician_id", "facts", ["politician_id"])
    op.create_index("ix_facts_source_key", "facts", ["source_key"])
    op.create_index("ix_facts_mention_id", "facts", ["mention_id"])
    op.create_index("ix_facts_published_at", "facts", ["published_at"])
    op.create_table(
        "fact_extractions",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("politician_id", UUID(as_uuid=False), sa.ForeignKey("politicians.id"), nullable=False),
        sa.Column("source_key", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("facts", sa.Integer(), nullable=True),
        sa.Column("dropped_unverified", sa.Integer(), nullable=True),
        sa.Column("chars_read", sa.Integer(), nullable=True),
        sa.Column("chars_total", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_fact_extractions_politician_id", "fact_extractions", ["politician_id"])


def downgrade() -> None:
    op.drop_index("ix_fact_extractions_politician_id", table_name="fact_extractions")
    op.drop_table("fact_extractions")
    for name in ("ix_facts_published_at", "ix_facts_mention_id", "ix_facts_source_key",
                 "ix_facts_politician_id"):
        op.drop_index(name, table_name="facts")
    op.drop_table("facts")

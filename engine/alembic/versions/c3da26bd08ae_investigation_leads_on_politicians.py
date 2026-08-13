"""investigation leads on politicians

Follow-up search queries raised by the investigator agent, chased on the next
run. Deliberately a column of its own rather than reusing `keywords`: keywords
are MATCHING terms used for entity linking, and a lead is a question to ask —
mixing the two would corrupt matching.

Note: autogenerate proposed dropping ix_documents_search_vector and
ix_events_title_trgm here. Those are created with raw DDL in the previous
migration (autogenerate can't see expression/GIN indexes it didn't author), and
dropping them would silently disable full-text evidence retrieval — the thing
the whole due-diligence promise rests on. Those drops are removed on purpose.

Revision ID: c3da26bd08ae
Revises: 30383dab9b6f
Create Date: 2026-08-13 21:45:53.699639

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3da26bd08ae'
down_revision: Union[str, None] = '30383dab9b6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'politicians',
        sa.Column('investigation_leads', sa.ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('politicians', 'investigation_leads')

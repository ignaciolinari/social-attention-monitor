"""add_ix_mentions_title_source_type

Revision ID: b5e0f3g7h8i9
Revises: a12dece03a79
Create Date: 2026-02-28 21:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b5e0f3g7h8i9'
down_revision: str | Sequence[str] | None = 'a12dece03a79'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add composite index on (title_id, source_type) for efficient source_type queries."""
    op.create_index(
        'ix_mentions_title_source_type',
        'mentions',
        ['title_id', 'source_type'],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove the (title_id, source_type) index."""
    op.drop_index('ix_mentions_title_source_type', table_name='mentions')

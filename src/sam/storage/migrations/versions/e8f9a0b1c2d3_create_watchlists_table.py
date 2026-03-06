"""create_watchlists_table

Revision ID: e8f9a0b1c2d3
Revises: c6d7e8f9a0b1
Create Date: 2026-03-05 17:01:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e8f9a0b1c2d3'
down_revision: str | Sequence[str] | None = 'c6d7e8f9a0b1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create watchlists table."""
    op.create_table(
        'watchlists',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('tmdb_ids', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Drop watchlists table."""
    op.drop_table('watchlists')

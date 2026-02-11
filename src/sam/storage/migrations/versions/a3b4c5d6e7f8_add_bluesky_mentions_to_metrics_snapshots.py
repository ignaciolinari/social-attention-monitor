"""add_bluesky_mentions_to_metrics_snapshots

Revision ID: a3b4c5d6e7f8
Revises: 21c675a10fdb
Create Date: 2026-02-07 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: str | Sequence[str] | None = '21c675a10fdb'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add bluesky_mentions column to metrics_snapshots."""
    op.add_column(
        'metrics_snapshots',
        sa.Column('bluesky_mentions', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Remove bluesky_mentions column from metrics_snapshots."""
    op.drop_column('metrics_snapshots', 'bluesky_mentions')

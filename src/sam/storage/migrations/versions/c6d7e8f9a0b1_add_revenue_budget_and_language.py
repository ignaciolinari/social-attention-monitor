"""add_revenue_budget_to_titles_and_detected_language_to_mentions

Revision ID: c6d7e8f9a0b1
Revises: b5e0f3g7h8i9
Create Date: 2026-03-05 17:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c6d7e8f9a0b1'
down_revision: str | Sequence[str] | None = 'b5e0f3g7h8i9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add revenue/budget to titles and detected_language to mentions."""
    op.add_column('titles', sa.Column('revenue', sa.BigInteger(), nullable=True))
    op.add_column('titles', sa.Column('budget', sa.BigInteger(), nullable=True))
    op.add_column('mentions', sa.Column('detected_language', sa.String(10), nullable=True))


def downgrade() -> None:
    """Remove revenue/budget from titles and detected_language from mentions."""
    op.drop_column('mentions', 'detected_language')
    op.drop_column('titles', 'budget')
    op.drop_column('titles', 'revenue')

"""merge heads after bluesky

Revision ID: a12dece03a79
Revises: 6f0b2d8c2a2a, a3b4c5d6e7f8
Create Date: 2026-02-07 03:10:31.796007

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a12dece03a79'
down_revision: str | Sequence[str] | None = ('6f0b2d8c2a2a', 'a3b4c5d6e7f8')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass

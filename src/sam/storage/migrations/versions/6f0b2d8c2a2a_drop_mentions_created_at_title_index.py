"""drop ix_mentions_created_at_title

Revision ID: 6f0b2d8c2a2a
Revises: 21c675a10fdb
Create Date: 2026-02-06

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6f0b2d8c2a2a"
down_revision: str | Sequence[str] | None = "21c675a10fdb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Windowing now uses collected_at, so the old composite index is unused.
    op.execute("DROP INDEX IF EXISTS ix_mentions_created_at_title")


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        "ix_mentions_created_at_title",
        "mentions",
        ["created_at", "title_id"],
        unique=False,
    )

"""Update mentions uniqueness to include title_id

Revision ID: 7b0f1b2c3d4e
Revises: 9408ea0bb59f
Create Date: 2026-01-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b0f1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "9408ea0bb59f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("uq_platform_source", "mentions", type_="unique")
    op.create_unique_constraint(
        "uq_platform_source_title",
        "mentions",
        ["platform", "source_id", "title_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_platform_source_title", "mentions", type_="unique")
    op.create_unique_constraint(
        "uq_platform_source",
        "mentions",
        ["platform", "source_id"],
    )

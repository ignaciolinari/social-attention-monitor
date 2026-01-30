"""Optionally enable TimescaleDB and hypertables

Revision ID: 3c8d4a1e9f22
Revises: 2f1c9a6b7d10
Create Date: 2026-01-30

This migration is designed to be safe on plain PostgreSQL:
- If the timescaledb extension isn't available, it will emit a NOTICE and continue.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3c8d4a1e9f22"
down_revision: Union[str, Sequence[str], None] = "2f1c9a6b7d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Enable TimescaleDB extension if available
    op.execute(
        """
        DO $$
        BEGIN
          CREATE EXTENSION IF NOT EXISTS timescaledb;
        EXCEPTION
          WHEN undefined_file OR feature_not_supported THEN
            RAISE NOTICE 'timescaledb extension not available, skipping';
        END $$;
        """
    )

    # Convert mentions table to hypertable if function exists
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'create_hypertable') THEN
            PERFORM create_hypertable('mentions', 'created_at', if_not_exists => TRUE);
          ELSE
            RAISE NOTICE 'create_hypertable not available, skipping';
          END IF;
        EXCEPTION
          WHEN undefined_function THEN
            RAISE NOTICE 'create_hypertable not available, skipping';
        END $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # We intentionally do not drop the extension or attempt to "un-hypertable"
    # in downgrade, as that can be destructive and environment-specific.
    pass


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
        DECLARE
          libs text;
        BEGIN
          BEGIN
            libs := current_setting('shared_preload_libraries', true);
          EXCEPTION
            WHEN insufficient_privilege THEN
              RAISE NOTICE 'insufficient privilege to read shared_preload_libraries, skipping timescaledb';
              RETURN;
            WHEN others THEN
              RAISE NOTICE 'could not read shared_preload_libraries: %, skipping', SQLERRM;
              RETURN;
          END;

          -- Some environments require preloading TimescaleDB via shared_preload_libraries.
          -- Attempting CREATE EXTENSION without preload can terminate the session (FATAL),
          -- aborting the migration transaction. Detect and skip instead.
          IF libs IS NULL OR position('timescaledb' in libs) = 0 THEN
            RAISE NOTICE 'timescaledb not preloaded (shared_preload_libraries=%), skipping', libs;
            RETURN;
          END IF;

          BEGIN
            CREATE EXTENSION IF NOT EXISTS timescaledb;
          EXCEPTION
            WHEN undefined_file OR feature_not_supported THEN
              RAISE NOTICE 'timescaledb extension not available, skipping';
            WHEN others THEN
              RAISE NOTICE 'timescaledb extension enable failed, skipping: %', SQLERRM;
          END;
        END $$;
        """
    )

    # Convert mentions table to hypertable if function exists
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'create_hypertable') THEN
            RAISE NOTICE 'create_hypertable not available, skipping';
            RETURN;
          END IF;

          -- TimescaleDB requires that any UNIQUE/PK index includes the partitioning column.
          -- Our `mentions` table uses a UUID primary key and additional unique constraints that
          -- do not include `created_at`, so hypertable creation may fail. Detect this and skip.
          IF EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            WHERE t.relname = 'mentions'
              AND c.contype = 'p'
              AND NOT EXISTS (
                SELECT 1
                FROM unnest(c.conkey) AS k(attnum)
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
                WHERE a.attname = 'created_at'
              )
          ) THEN
            RAISE NOTICE 'mentions primary key does not include created_at; skipping hypertable';
            RETURN;
          END IF;

          PERFORM create_hypertable('mentions', 'created_at', if_not_exists => TRUE);
        EXCEPTION
          WHEN undefined_function OR feature_not_supported THEN
            RAISE NOTICE 'create_hypertable not available, skipping';
          WHEN others THEN
            -- Keep this migration non-blocking in CI/dev environments.
            RAISE NOTICE 'create_hypertable failed, skipping: %', SQLERRM;
        END $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # We intentionally do not drop the extension or attempt to "un-hypertable"
    # in downgrade, as that can be destructive and environment-specific.
    pass

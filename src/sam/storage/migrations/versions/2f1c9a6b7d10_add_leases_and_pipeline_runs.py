"""Add leases and pipeline run tracking

Revision ID: 2f1c9a6b7d10
Revises: 7b0f1b2c3d4e
Create Date: 2026-01-30

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "2f1c9a6b7d10"
down_revision: Union[str, Sequence[str], None] = "7b0f1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "leases",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index(op.f("ix_leases_expires_at"), "leases", ["expires_at"], unique=False)
    op.create_index(op.f("ix_leases_owner_id"), "leases", ["owner_id"], unique=False)

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pipeline_runs_job_name"), "pipeline_runs", ["job_name"], unique=False)
    op.create_index(op.f("ix_pipeline_runs_owner_id"), "pipeline_runs", ["owner_id"], unique=False)
    op.create_index(op.f("ix_pipeline_runs_started_at"), "pipeline_runs", ["started_at"], unique=False)
    op.create_index(
        "ix_pipeline_runs_job_started",
        "pipeline_runs",
        ["job_name", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_pipeline_runs_owner_started",
        "pipeline_runs",
        ["owner_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_pipeline_runs_owner_started", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_job_started", table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_started_at"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_owner_id"), table_name="pipeline_runs")
    op.drop_index(op.f("ix_pipeline_runs_job_name"), table_name="pipeline_runs")
    op.drop_table("pipeline_runs")

    op.drop_index(op.f("ix_leases_owner_id"), table_name="leases")
    op.drop_index(op.f("ix_leases_expires_at"), table_name="leases")
    op.drop_table("leases")

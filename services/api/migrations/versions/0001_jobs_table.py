"""Create jobs table.

Revision ID: 0001
Revises:
Create Date: 2026-05-15 00:00:00.000000

Maps the Job Pydantic model (shared/models.py) into SQLite.  Dates are stored
as DATETIME columns (SQLAlchemy emits ISO-8601 text for SQLite).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("aoi_id", sa.String(36), nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("jobs")

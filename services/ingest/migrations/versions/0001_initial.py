"""Create initial scenes table.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-15 00:00:00.000000

The ``scenes`` table mirrors the ``Scene`` Pydantic model defined in
``shared/models.py``. UUIDs are stored as TEXT (SQLite has no native UUID
type). Datetimes are ISO 8601 TEXT strings.

``cdse_product_id`` stores the CDSE OData product UUID so fetch_scene can
construct the OData download URL without a second network round-trip.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "scenes",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True),
        sa.Column(
            "platform",
            sa.Text(),
            nullable=False,
            comment="S1 or S2",
        ),
        sa.Column("product_type", sa.Text(), nullable=False),
        sa.Column("sensing_start", sa.Text(), nullable=False),
        sa.Column("sensing_end", sa.Text(), nullable=False),
        sa.Column("polarization", sa.Text(), nullable=True),
        sa.Column("orbit_direction", sa.Text(), nullable=True),
        sa.Column(
            "footprint_json",
            sa.Text(),
            nullable=False,
            comment="GeoJSON geometry as JSON string",
        ),
        sa.Column(
            "cdse_product_id",
            sa.Text(),
            nullable=True,
            comment="CDSE OData product UUID for downloads",
        ),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
        sa.CheckConstraint("platform IN ('S1', 'S2')", name="ck_scenes_platform"),
        sa.CheckConstraint(
            "orbit_direction IN ('ASCENDING', 'DESCENDING') OR orbit_direction IS NULL",
            name="ck_scenes_orbit_direction",
        ),
    )
    op.create_index("idx_scenes_sensing_start", "scenes", ["sensing_start"])


def downgrade() -> None:
    op.drop_index("idx_scenes_sensing_start", table_name="scenes")
    op.drop_table("scenes")

"""add implement preset range columns

Revision ID: h1i2j3k4l5m6
Revises: g2h3i4j5k6l7
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h1i2j3k4l5m6"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("implements", sa.Column("preset_speed_kmh_min", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("preset_speed_kmh_max", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("preset_depth_cm_min", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("preset_depth_cm_max", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("implements", "preset_depth_cm_max")
    op.drop_column("implements", "preset_depth_cm_min")
    op.drop_column("implements", "preset_speed_kmh_max")
    op.drop_column("implements", "preset_speed_kmh_min")

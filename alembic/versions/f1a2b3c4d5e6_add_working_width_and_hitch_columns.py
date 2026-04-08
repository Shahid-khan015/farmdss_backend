"""add nullable tractor and implement capacity/hitch columns

Revision ID: f1a2b3c4d5e6
Revises: e8c1f4a2b7d3
Create Date: 2026-03-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e8c1f4a2b7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("implements", sa.Column("working_width_m", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("hitch_type", sa.String(length=30), nullable=True))
    op.create_check_constraint(
        "ck_implements_hitch_type",
        "implements",
        "hitch_type IN ('3-point','drawbar','pto-only','integral')",
    )

    op.add_column("tractors", sa.Column("pto_rpm_min", sa.Integer(), nullable=True))
    op.add_column("tractors", sa.Column("pto_rpm_max", sa.Integer(), nullable=True))
    op.add_column("tractors", sa.Column("tow_capacity_kg", sa.Float(), nullable=True))
    op.add_column("tractors", sa.Column("hitch_type", sa.String(length=30), nullable=True))
    op.create_check_constraint(
        "ck_tractors_hitch_type",
        "tractors",
        "hitch_type IN ('3-point','drawbar','pto-only','integral')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tractors_hitch_type", "tractors", type_="check")
    op.drop_column("tractors", "hitch_type")
    op.drop_column("tractors", "tow_capacity_kg")
    op.drop_column("tractors", "pto_rpm_max")
    op.drop_column("tractors", "pto_rpm_min")

    op.drop_constraint("ck_implements_hitch_type", "implements", type_="check")
    op.drop_column("implements", "hitch_type")
    op.drop_column("implements", "working_width_m")

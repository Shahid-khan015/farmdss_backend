"""add implement presets and operation charges

Revision ID: c7d8e9f0a1b2
Revises: a9b8c7d6e5f4
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "a9b8c7d6e5f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("implements", sa.Column("preset_speed_kmh", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("preset_depth_cm", sa.Float(), nullable=True))
    op.add_column("implements", sa.Column("preset_gearbox_temp_max_c", sa.Float(), nullable=True))

    op.create_table(
        "operation_charges",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("charge_per_ha", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default=sa.text("'INR'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('Tillage','Sowing','Spraying','Weeding','Harvesting','Threshing','Grading')",
            name="ck_operation_charges_operation_type",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "operation_type", name="uq_operation_charges_owner_operation_type"),
    )


def downgrade() -> None:
    op.drop_table("operation_charges")
    op.drop_column("implements", "preset_gearbox_temp_max_c")
    op.drop_column("implements", "preset_depth_cm")
    op.drop_column("implements", "preset_speed_kmh")

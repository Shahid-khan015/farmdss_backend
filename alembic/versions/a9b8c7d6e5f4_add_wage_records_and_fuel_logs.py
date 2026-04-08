"""add wage_records and fuel_logs

Revision ID: a9b8c7d6e5f4
Revises: f1a2b3c4d5e6
Create Date: 2026-04-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9b8c7d6e5f4"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wage_records",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("rate_type", sa.String(length=10), nullable=False),
        sa.Column("rate_amount", sa.Float(), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=True),
        sa.Column("duration_hours", sa.Float(), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=True),
        sa.Column("approved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disputed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("dispute_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "rate_type IN ('hourly','per_ha')",
            name="ck_wage_records_rate_type",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_wage_records_session_id"),
    )
    op.create_index("idx_wage_records_operator_id", "wage_records", ["operator_id"], unique=False)

    op.create_table(
        "fuel_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tractor_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("litres", sa.Float(), nullable=False),
        sa.Column("refilled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cost_per_litre", sa.Float(), nullable=True),
        sa.Column("total_cost", sa.Float(), nullable=True),
        sa.Column("entered_by", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tractor_id"], ["tractors.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["entered_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_fuel_logs_tractor_id", "fuel_logs", ["tractor_id"], unique=False)
    op.create_index("idx_fuel_logs_session_id", "fuel_logs", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_fuel_logs_session_id", table_name="fuel_logs")
    op.drop_index("idx_fuel_logs_tractor_id", table_name="fuel_logs")
    op.drop_table("fuel_logs")
    op.drop_index("idx_wage_records_operator_id", table_name="wage_records")
    op.drop_table("wage_records")

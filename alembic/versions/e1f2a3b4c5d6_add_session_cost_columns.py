"""add session cost columns

Revision ID: e1f2a3b4c5d6
Revises: c7d8e9f0a1b2
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("total_cost_inr", sa.Float(), nullable=True))
    op.add_column("sessions", sa.Column("charge_per_ha_applied", sa.Float(), nullable=True))
    op.add_column("sessions", sa.Column("cost_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "cost_note")
    op.drop_column("sessions", "charge_per_ha_applied")
    op.drop_column("sessions", "total_cost_inr")

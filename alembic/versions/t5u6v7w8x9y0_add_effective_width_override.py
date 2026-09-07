"""add effective_width_override_m column to simulations for passive-passive
field-capacity swath override (F5)

Revision ID: t5u6v7w8x9y0
Revises: n9o0p1q2r3s4
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "t5u6v7w8x9y0"
down_revision = "n9o0p1q2r3s4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "simulations",
        sa.Column("effective_width_override_m", sa.DECIMAL(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulations", "effective_width_override_m")

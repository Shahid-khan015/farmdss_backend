"""add charge_per_hour for Threshing and Grading operation charges

Revision ID: g2h3i4j5k6l7
Revises: e1f2a3b4c5d6
Create Date: 2026-04-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g2h3i4j5k6l7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operation_charges", sa.Column("charge_per_hour", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("operation_charges", "charge_per_hour")

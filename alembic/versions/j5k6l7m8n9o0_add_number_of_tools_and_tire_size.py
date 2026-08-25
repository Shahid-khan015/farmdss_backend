"""add implements.number_of_tools and tire size designations

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-08-23

Notes
-----
`implements.number_of_tools` is ASABE D497 Eq. 3.1's `W` for implement classes
tabulated per tool (cultivators). Reading `W` as metres for those makes draft
size-independent and drives a rotavator combination's effective draft negative;
see `app.core.constants.DRAFT_WIDTH_IS_TOOL_COUNT`.

`tire_specifications.*_tire_size` records the designation ("13.6 x 28"). It is
not used by the engine, but it is the only field from which the overall diameter
can be re-derived or checked -- its absence is why rim diameters were stored in
the overall-diameter columns unnoticed.

Both columns are nullable: existing rows predate them and must keep working.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "j5k6l7m8n9o0"
down_revision = "i4j5k6l7m8n9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("implements", sa.Column("number_of_tools", sa.Integer(), nullable=True))
    op.add_column(
        "tire_specifications", sa.Column("front_tire_size", sa.String(length=30), nullable=True)
    )
    op.add_column(
        "tire_specifications", sa.Column("rear_tire_size", sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tire_specifications", "rear_tire_size")
    op.drop_column("tire_specifications", "front_tire_size")
    op.drop_column("implements", "number_of_tools")

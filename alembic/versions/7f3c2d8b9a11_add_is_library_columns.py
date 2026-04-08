"""add is_library columns to tractors and implements

Revision ID: 7f3c2d8b9a11
Revises: 06fb74fa2fc4
Create Date: 2026-03-17 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "7f3c2d8b9a11"
down_revision = "06fb74fa2fc4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tractors",
        sa.Column("is_library", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(op.f("ix_tractors_is_library"), "tractors", ["is_library"], unique=False)

    op.add_column(
        "implements",
        sa.Column("is_library", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(op.f("ix_implements_is_library"), "implements", ["is_library"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_implements_is_library"), table_name="implements")
    op.drop_column("implements", "is_library")

    op.drop_index(op.f("ix_tractors_is_library"), table_name="tractors")
    op.drop_column("tractors", "is_library")

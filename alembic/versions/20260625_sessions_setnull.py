"""make sessions.tractor_id nullable and set FK to SET NULL

Revision ID: 20260625_sessions_setnull
Revises: e8c1f4a2b7d3
Create Date: 2026-06-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260625_sessions_setnull"
down_revision = "e8c1f4a2b7d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop any existing FK from sessions.tractor_id -> tractors(id)
    fks = [fk for fk in inspector.get_foreign_keys("sessions")]
    for fk in fks:
        cons_cols = fk.get("constrained_columns") or []
        if cons_cols == ["tractor_id"] and fk.get("referred_table") == "tractors":
            name = fk.get("name")
            if name:
                op.drop_constraint(name, "sessions", type_="foreignkey")

    # Make column nullable (if not already)
    op.alter_column("sessions", "tractor_id", existing_type=sa.Uuid(), nullable=True)

    # Create FK with ON DELETE SET NULL
    # Use a deterministic name so future migrations can find it
    op.create_foreign_key(
        "fk_sessions_tractor_id_tractors",
        "sessions",
        "tractors",
        ["tractor_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop our FK
    fk_names = [fk.get("name") for fk in inspector.get_foreign_keys("sessions") if fk.get("referred_table") == "tractors"]
    for name in fk_names:
        if name:
            op.drop_constraint(name, "sessions", type_="foreignkey")

    # Recreate a RESTRICT FK and make column non-nullable to restore previous behavior
    op.create_foreign_key(
        "fk_sessions_tractor_id_tractors",
        "sessions",
        "tractors",
        ["tractor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.alter_column("sessions", "tractor_id", existing_type=sa.Uuid(), nullable=False)

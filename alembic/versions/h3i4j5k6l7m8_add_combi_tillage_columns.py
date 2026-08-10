"""add combination_type, interaction_coefficient, implement_2_id and rotor spec
columns to simulations for passive-passive / active-passive combi-tillage

Revision ID: h3i4j5k6l7m8
Revises: g2h3i4j5k6l7
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h3i4j5k6l7m8"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None

_COMBINATION_TYPE_ENUM = sa.Enum(
    "single", "passive_passive", "active_passive", name="simulation_combination_type"
)


def upgrade() -> None:
    _COMBINATION_TYPE_ENUM.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "simulations",
        sa.Column(
            "combination_type",
            _COMBINATION_TYPE_ENUM,
            nullable=False,
            server_default="single",
        ),
    )
    op.add_column("simulations", sa.Column("implement_2_id", sa.Uuid(), nullable=True))
    op.add_column("simulations", sa.Column("interaction_coefficient", sa.DECIMAL(), nullable=True))

    op.add_column("simulations", sa.Column("rotor_weight", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_cg_distance_from_hitch", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_mechanical_resistance", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_efficiency", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_pto_power", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_speed", sa.DECIMAL(), nullable=True))
    op.add_column("simulations", sa.Column("rotor_dynamic_vertical_force", sa.DECIMAL(), nullable=True))

    op.create_foreign_key(
        "fk_simulations_implement_2_id_implements",
        "simulations",
        "implements",
        ["implement_2_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_simulations_implement_2_id", "simulations", ["implement_2_id"])


def downgrade() -> None:
    op.drop_index("ix_simulations_implement_2_id", table_name="simulations")
    op.drop_constraint("fk_simulations_implement_2_id_implements", "simulations", type_="foreignkey")

    op.drop_column("simulations", "rotor_dynamic_vertical_force")
    op.drop_column("simulations", "rotor_speed")
    op.drop_column("simulations", "rotor_pto_power")
    op.drop_column("simulations", "rotor_efficiency")
    op.drop_column("simulations", "rotor_mechanical_resistance")
    op.drop_column("simulations", "rotor_cg_distance_from_hitch")
    op.drop_column("simulations", "rotor_weight")

    op.drop_column("simulations", "interaction_coefficient")
    op.drop_column("simulations", "implement_2_id")
    op.drop_column("simulations", "combination_type")

    _COMBINATION_TYPE_ENUM.drop(op.get_bind(), checkfirst=True)

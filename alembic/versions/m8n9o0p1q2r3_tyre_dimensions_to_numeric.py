"""store tyre dimensions as NUMERIC(7,2) and set the exact reference geometry

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
Create Date: 2026-08-23

Why
---
The eight tyre dimension columns were INTEGER millimetres. Real tyre specs carry
fractional millimetres (a 12.4 x 28 is 1226.31 mm, not 1226; its section width is
314.96 mm, not 314), so every value was truncated on the way in.

`Bn = CI*b*d/Wd` is linear in both `b` and `d`, so those truncations compound
through the entire traction chain. Measured against the reference geometry on
Eicher 368 + MB 2-bottom: Bn -0.59%, front axle load -0.93%, slip -0.29%.
Small, but avoidable and entirely an artefact of the column type.

This revision also repairs a gap in revision k6l7m8n9o0p1, which updated the
diameters and radii but **not** the four section-width columns. That left
upgraded databases at 314/152 where a fresh install seeded 315/152 -- an
internal seed-vs-database inconsistency on 8 tractors.

Four tyre sizes are absent from the reference library (Captain front 5.2 x 14,
Eicher 6100 rear 16.9 x 30, Mahindra 2515 DI 5.0 x 15 and 12.4 x 24). Their
geometry is extrapolated from the reference's own eight sizes, which are highly
self-consistent: mean aspect ratio 0.8154 (sd 0.016), SLR/R 0.9232 (sd 0.009),
RR/R 0.9525 (sd 0.006).

The downgrade restores INTEGER, which necessarily re-truncates. That is
acceptable only because it returns the column to its previous, less precise
state; the values are not otherwise altered.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "m8n9o0p1q2r3"
down_revision = "l7m8n9o0p1q2"
branch_labels = None
depends_on = None

DIMENSION_COLUMNS = (
    "front_overall_diameter",
    "front_section_width",
    "front_static_loaded_radius",
    "front_rolling_radius",
    "rear_overall_diameter",
    "rear_section_width",
    "rear_static_loaded_radius",
    "rear_rolling_radius",
)

# designation -> (section_width, overall_diameter, static_loaded_radius, rolling_radius) mm
TYRE = {
    "5.2 X 12": (127.00, 513.59, 237.74, 245.01),
    "5.2 x 14": (132.08, 571.00, 263.58, 271.93),    # derived
    "5.0 x 15": (127.00, 588.12, 271.48, 280.08),    # derived
    "6 x 16": (152.40, 659.38, 306.83, 315.58),
    "7.5 x 16": (190.50, 716.53, 329.69, 340.56),
    "8 x 18": (203.20, 789.43, 364.24, 375.85),
    "12.4 x 24": (314.96, 1123.25, 518.51, 534.93),  # derived
    "12.4 x 28": (314.96, 1226.31, 565.91, 583.91),
    "13.6 x 28": (345.44, 1272.03, 584.20, 603.88),
    "16.9 x 30": (429.26, 1462.06, 674.91, 696.28),  # derived
}


def upgrade() -> None:
    for column in DIMENSION_COLUMNS:
        op.alter_column(
            "tire_specifications",
            column,
            existing_type=sa.Integer(),
            type_=sa.Numeric(7, 2),
            existing_nullable=True,
            postgresql_using="{0}::numeric(7,2)".format(column),
        )

    conn = op.get_bind()
    # Set the exact geometry for every library tyre, keyed on the size designation
    # written by revision k6l7m8n9o0p1. Rows without one (user-entered equipment)
    # are left untouched.
    for size, (sw, od, slr, rr) in TYRE.items():
        conn.execute(
            sa.text(
                "UPDATE tire_specifications ts SET "
                "front_section_width = :sw, front_overall_diameter = :od, "
                "front_static_loaded_radius = :slr, front_rolling_radius = :rr "
                "FROM tractors t "
                "WHERE ts.tractor_id = t.id AND t.is_library = true "
                "AND ts.front_tire_size = :size"
            ),
            {"sw": sw, "od": od, "slr": slr, "rr": rr, "size": size},
        )
        conn.execute(
            sa.text(
                "UPDATE tire_specifications ts SET "
                "rear_section_width = :sw, rear_overall_diameter = :od, "
                "rear_static_loaded_radius = :slr, rear_rolling_radius = :rr "
                "FROM tractors t "
                "WHERE ts.tractor_id = t.id AND t.is_library = true "
                "AND ts.rear_tire_size = :size"
            ),
            {"sw": sw, "od": od, "slr": slr, "rr": rr, "size": size},
        )

    # Keep the tractor-level rear rolling radius (metres) in step with the tyre
    # record; the engine uses it as a fallback when the tyre value is missing, and
    # the two disagreeing is exactly how drift starts.
    conn.execute(
        sa.text(
            "UPDATE tractors t SET rear_wheel_rolling_radius = ts.rear_rolling_radius / 1000.0 "
            "FROM tire_specifications ts "
            "WHERE ts.tractor_id = t.id AND t.is_library = true "
            "AND ts.rear_rolling_radius IS NOT NULL"
        )
    )


def downgrade() -> None:
    for column in DIMENSION_COLUMNS:
        op.alter_column(
            "tire_specifications",
            column,
            existing_type=sa.Numeric(7, 2),
            type_=sa.Integer(),
            existing_nullable=True,
            postgresql_using="round({0})::integer".format(column),
        )

"""correct library tractor tyre/CG data and implement geometry

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-08-23

Why this is a data migration and not just a seed change
-------------------------------------------------------
`seed_library_if_empty` only inserts when the catalogue is empty, so correcting
`seed_library.py` alone leaves every already-deployed database on the old,
wrong values. This migration brings existing library rows in line.

What was wrong
--------------
1. `tire_specifications.*_overall_diameter` held the **rim** diameter, not the
   tyre's overall diameter (Eicher 368 stored 406/711 mm -- exactly 16"/28"
   rims -- against a true 659/1226 mm). Static-loaded and rolling radii were
   built off the same wrong base. `Bn = CI*b*d/Wd` is linear in `d`, so this
   understated the wheel numeric on both axles for every simulation.
2. `tractors.cg_distance_from_rear` disagreed with each tractor's own axle
   weights by 13-52%. Static balance fixes it exactly: `Xcgt = Wf*L/(Wf+Wr)`.
3. Cultivators had no `number_of_tools` (ASABE D497's `W` for tined tools),
   widths implying a 133-141 mm tine spacing (textbook is 200-250 mm), and
   disc-harrow / plough CG distances outside the reference band.

Only rows with `is_library = true` are touched; user-created equipment is left
alone. Matching is by name, so a renamed library row is skipped rather than
mis-updated. The downgrade is intentionally a no-op: the previous values were
incorrect and restoring them has no value.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "k6l7m8n9o0p1"
down_revision = "j5k6l7m8n9o0"
branch_labels = None
depends_on = None

# name -> (front_size, front_od, front_slr, front_rr,
#          rear_size,  rear_od,  rear_slr,  rear_rr)   [mm]
TRACTOR_TYRES = {
    "Captain DI 2600":             ("5.2 x 14", 571, 264, 272, "8 x 18",    789, 364, 376),
    "Mitsubishi MT 180 D":         ("5.2 X 12", 514, 238, 245, "8 x 18",    789, 364, 376),
    "Eicher 242 NC":               ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 243 NC":               ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 312 NC":               ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 364 P":                ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 368":                  ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 485":                  ("6 x 16",   659, 307, 316, "12.4 x 28", 1226, 566, 584),
    "Eicher 586":                  ("6 x 16",   659, 307, 316, "13.6 x 28", 1272, 584, 604),
    "Eicher 6100":                 ("7.5 x 16", 717, 330, 341, "16.9 x 30", 1462, 675, 696),
    "Mahindra & Mahindra 2515 DI": ("5.0 x 15", 588, 271, 280, "12.4 x 24", 1123, 519, 535),
}

# name -> column overrides
IMPLEMENTS = {
    "Light Cultivator (9 Tines)":    {"width": 2.2,  "cg_distance_from_hitch": 0.46, "number_of_tools": 9},
    "Medium Cultivator (13 Tines)":  {"width": 3.13, "cg_distance_from_hitch": 0.46, "number_of_tools": 13},
    "Heavy Cultivator (17 Tines)":   {"width": 4.15, "cg_distance_from_hitch": 0.46, "number_of_tools": 17},
    "Light Disc Harrow (16 Discs)":  {"cg_distance_from_hitch": 0.65},
    "Medium Disc Harrow (24 Discs)": {"cg_distance_from_hitch": 0.65},
    "Heavy Disc Harrow (32 Discs)":  {"cg_distance_from_hitch": 0.65},
    "2-Bottom MB Plough":            {"cg_distance_from_hitch": 0.55},
    "3-Bottom MB Plough":            {"cg_distance_from_hitch": 0.70},
    "2-Disc Plough":                 {"cg_distance_from_hitch": 0.50},
    "3-Disc Plough":                 {"cg_distance_from_hitch": 0.65},
}


def upgrade() -> None:
    conn = op.get_bind()

    for name, (fs, fod, fslr, frr, rs, rod, rslr, rrr) in TRACTOR_TYRES.items():
        row = conn.execute(
            sa.text(
                "SELECT id, wheelbase, front_axle_weight, rear_axle_weight "
                "FROM tractors WHERE name = :n AND is_library = true"
            ),
            {"n": name},
        ).fetchone()
        if row is None:
            continue
        tractor_id, wheelbase, front_kg, rear_kg = row

        # Static balance: the CG sits at Wf*L/(Wf+Wr) ahead of the rear axle.
        if wheelbase and front_kg and rear_kg and (front_kg + rear_kg) > 0:
            cg = float(front_kg) * float(wheelbase) / (float(front_kg) + float(rear_kg))
            conn.execute(
                sa.text(
                    "UPDATE tractors SET cg_distance_from_rear = :cg, "
                    "rear_wheel_rolling_radius = :rr WHERE id = :id"
                ),
                {"cg": round(cg, 4), "rr": round(rrr / 1000.0, 3), "id": tractor_id},
            )

        conn.execute(
            sa.text(
                "UPDATE tire_specifications SET "
                "front_tire_size = :fs, front_overall_diameter = :fod, "
                "front_static_loaded_radius = :fslr, front_rolling_radius = :frr, "
                "rear_tire_size = :rs, rear_overall_diameter = :rod, "
                "rear_static_loaded_radius = :rslr, rear_rolling_radius = :rrr "
                "WHERE tractor_id = :id"
            ),
            {
                "fs": fs, "fod": fod, "fslr": fslr, "frr": frr,
                "rs": rs, "rod": rod, "rslr": rslr, "rrr": rrr,
                "id": tractor_id,
            },
        )

    for name, fields in IMPLEMENTS.items():
        assignments = ", ".join("{0} = :{0}".format(k) for k in fields)
        params = dict(fields)
        params["n"] = name
        conn.execute(
            sa.text(
                "UPDATE implements SET {0} WHERE name = :n AND is_library = true".format(
                    assignments
                )
            ),
            params,
        )


def downgrade() -> None:
    # Deliberately a no-op: the prior values were incorrect (rim diameters in the
    # overall-diameter columns, CG inconsistent with the axle weights), so there
    # is nothing worth restoring.
    pass

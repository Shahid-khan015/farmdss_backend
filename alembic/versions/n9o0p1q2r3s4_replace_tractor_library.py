"""replace the tractor library with the 18-tractor reference set

Revision ID: n9o0p1q2r3s4
Revises: m8n9o0p1q2r3
Create Date: 2026-08-24

Why a migration and not just a seed change
------------------------------------------
`seed_library_if_empty` only inserts into an EMPTY catalogue, so editing
`seed_library.py` alone never reaches a populated database. That is exactly how
the earlier catalogue drift happened.

Retire, do not delete
---------------------
The 11 tractors previously in the library are referenced by 54 `simulations`
(FK ON DELETE CASCADE) and 43 `sessions`. Deleting them would destroy that
history. They are instead marked `is_library = false`: the rows survive, every
foreign key still resolves, and they simply stop appearing in the picker.

Two corrected source values
---------------------------
The supplied table had `Front + Rear != Total` on two rows. In both, the stated
CG matches `Wf*L/Total` rather than `Wf*L/(Wf+Wr)`, identifying `Total` as the
trusted figure and the rear-axle weight as the transcription error:

    Farmtrac 45        rear 1005 -> 1085   (= 1845 - 760),  CG 0.8008 ~ 0.80
    New Holland 6010   rear 1565 -> 1615   (= 2580 - 965),  CG 0.7761 ~ 0.775

With those corrections all 18 satisfy the static-balance invariant within 0.6%
and all 18 pass the torque-backup check P(Tmax) > PTO.

Inserts are guarded by a name check, so the revision is idempotent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "n9o0p1q2r3s4"
down_revision = "m8n9o0p1q2r3"
branch_labels = None
depends_on = None

# name, manufacturer, model, pto_kw, rpm, tmax, wheelbase, wf, wr, hitch, xcgt, rear_rr_m,
# front_size, front_od, front_sw, front_slr, front_rr,
# rear_size,  rear_od,  rear_sw,  rear_slr,  rear_rr
TRACTORS = [
    ('VST Shakti MT 180D HS/JAI', 'VST Shakti', 'MT 180D HS/JAI', 12.0, 2700, 45.5, 1.42, 315, 440, 0.72, 0.59, 0.37585,
     '5.2 X 12', 513.59, 127.0, 237.74, 245.01, '8 x 18', 789.43, 203.2, 364.24, 375.85),
    ('Mahindra & Mahindra B 275 DI', 'Mahindra & Mahindra', 'B 275 DI', 25.5, 2600, 105.2, 1.83, 710, 1080, 0.72, 0.73, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('PTL 735 FE', 'PTL', '735 FE', 25.3, 2000, 139.3, 1.955, 675, 1110, 0.72, 0.74, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('PTL 744 FE', 'PTL', '744 FE', 30.4, 2000, 165.6, 1.955, 750, 1180, 0.72, 0.76, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('PTL 855 FE', 'PTL', '855 FE', 33.0, 2000, 181.7, 1.95, 755, 1160, 0.72, 0.77, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Eicher 368', 'Eicher', '368', 25.0, 2150, 132.5, 1.985, 720, 1100, 0.72, 0.79, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('Eicher 485', 'Eicher', '485', 28.7, 2150, 148.7, 2.07, 695, 1205, 0.72, 0.76, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('Farmtrac 45', 'Farmtrac', '45', 27.1, 2000, 144.1, 1.944, 760, 1085, 0.72, 0.8, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Farmtrac 55', 'Farmtrac', '55', 30.5, 2000, 157.4, 1.935, 755, 1160, 0.72, 0.76, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('TAFE MF 245', 'TAFE', 'MF 245', 30.2, 2250, 157.8, 1.814, 655, 1040, 0.72, 0.7, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('TAFE MF 241 DI(J)', 'TAFE', 'MF 241 DI(J)', 28.3, 2000, 156.0, 1.82, 680, 1020, 0.72, 0.73, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('New Holland 3230 NX', 'New Holland', '3230 NX', 28.6, 2000, 158.6, 1.91, 680, 1000, 0.72, 0.77, 0.58391,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '12.4 x 28', 1226.31, 314.96, 565.91, 583.91),
    ('New Holland 3630 TX', 'New Holland', '3630 TX', 33.8, 2500, 141.7, 2.065, 840, 1250, 0.72, 0.83, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Sonalika 750 DI', 'Sonalika', '750 DI', 28.9, 2250, 140.6, 1.94, 790, 1180, 0.72, 0.78, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Sonalika 750 DI III', 'Sonalika', '750 DI III', 31.3, 2100, 155.9, 2.065, 925, 1205, 0.72, 0.9, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Sonalika DI 55', 'Sonalika', 'DI 55', 36.6, 2100, 185.5, 2.085, 930, 1205, 0.72, 0.91, 0.60388,
     '6 x 16', 659.38, 152.4, 306.83, 315.58, '13.6 x 28', 1272.03, 345.44, 584.2, 603.88),
    ('Johndeere 5310', 'John Deere', '5310', 37.4, 2400, 178.0, 2.05, 755, 1400, 0.72, 0.72, 0.65877,
     '6.5 x 20', 786.13, 165.1, 368.3, 377.82, '16.9 x 28', 1397.76, 429.26, 634.49, 658.77),
    ('New Holland 6010', 'New Holland', '6010', 39.0, 2300, 236.93, 2.075, 965, 1615, 0.72, 0.775, 0.65877,
     '7.5 x 16', 716.53, 190.5, 329.69, 340.56, '16.9 x 28', 1397.76, 429.26, 634.49, 658.77),
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Retire the previous library. Rows are kept so the 54 simulations and 43
    # sessions referencing them keep resolving.
    conn.execute(sa.text("UPDATE tractors SET is_library = false WHERE is_library = true"))

    for row in TRACTORS:
        (name, mfr, model, kw, rpm, tmax, wb, wf, wr, hd, xcgt, rear_rr_m,
         fs, fod, fsw, fslr, frr, rs, rod, rsw, rslr, rrr) = row

        existing = conn.execute(
            sa.text("SELECT id FROM tractors WHERE name = :n AND is_library = true"),
            {"n": name},
        ).fetchone()
        if existing is not None:
            continue

        tractor_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO tractors (id, name, manufacturer, model, pto_power, "
                "rated_engine_speed, max_engine_torque, wheelbase, front_axle_weight, "
                "rear_axle_weight, hitch_distance_from_rear, cg_distance_from_rear, "
                "rear_wheel_rolling_radius, drive_mode, transmission_efficiency, "
                "power_reserve, is_library, created_at, updated_at) VALUES "
                "(:id, :n, :mfr, :model, :kw, :rpm, :tmax, :wb, :wf, :wr, :hd, :xcgt, "
                ":rrm, 'WD2', 86, 20, true, :ts, :ts)"
            ),
            {"id": tractor_id, "n": name, "mfr": mfr, "model": model, "kw": kw, "rpm": rpm,
             "tmax": tmax, "wb": wb, "wf": wf, "wr": wr, "hd": hd, "xcgt": xcgt,
             "rrm": rear_rr_m, "ts": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO tire_specifications (id, tractor_id, tire_type, "
                "front_tire_size, front_overall_diameter, front_section_width, "
                "front_static_loaded_radius, front_rolling_radius, "
                "rear_tire_size, rear_overall_diameter, rear_section_width, "
                "rear_static_loaded_radius, rear_rolling_radius, created_at, updated_at) "
                "VALUES (:id, :tid, 'BIAS_PLY', :fs, :fod, :fsw, :fslr, :frr, "
                ":rs, :rod, :rsw, :rslr, :rrr, :ts, :ts)"
            ),
            {"id": uuid.uuid4(), "tid": tractor_id, "fs": fs, "fod": fod, "fsw": fsw,
             "fslr": fslr, "frr": frr, "rs": rs, "rod": rod, "rsw": rsw, "rslr": rslr,
             "rrr": rrr, "ts": now},
        )


def downgrade() -> None:
    conn = op.get_bind()
    names = [r[0] for r in TRACTORS]
    conn.execute(
        sa.text("DELETE FROM tractors WHERE is_library = true AND name = ANY(:names)"),
        {"names": names},
    )
    conn.execute(
        sa.text("UPDATE tractors SET is_library = true WHERE is_library = false "
                "AND name <> ALL(:names)"),
        {"names": names},
    )

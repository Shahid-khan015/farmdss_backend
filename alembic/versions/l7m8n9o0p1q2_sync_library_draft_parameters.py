"""sync library implement draft parameters (ASAE A/B/C and Py/D) to the audited values

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-08-23

Why
---
`seed_library.py` was corrected to the audited ASABE D497 draft parameters, but
`seed_library_if_empty` only inserts into an EMPTY catalogue -- so every already
-deployed database kept the original placeholder values. An end-to-end run
against live Postgres surfaced it: a 9-tine cultivator produced 72,675 N of
draft instead of ~6,350 N, because the row still held `A=200, B=30, C=5`
(0.85 x (200 + 30*5 + 5*25) x 9 x 20 = 72,675 exactly).

All 13 passive library implements were affected, on both the A/B/C parameters
and Py/D:

    type          was (A/B/C, Py/D)          now (A/B/C, Py/D)
    MB Plough     500 / 50  / 8   , 0.65     652 / 0    / 5.1 , 0.20
    Disc Plough   400 / 45  / 7   , 0.60     124 / 6.4  / 0   , 0.00
    Disc Harrow   150 / 25  / 4   , 0.50     254 / 13.2 / 0   , 0.00
    Cultivator    200 / 30  / 5   , 0.55      32 / 1.9  / 0   , 0.20

Keyed on `implement_type` rather than name so any library row of a given class is
corrected, including ones added since. Only `is_library = true` rows are touched;
user-created implements keep whatever the user entered.

`implement_type` is a native Postgres enum whose labels are the Python enum
MEMBER NAMES (the model omits `values_callable`), so the comparison below uses
'MB_PLOUGH' etc., not the display values.

The downgrade is a no-op: the previous values were placeholders with no source.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "l7m8n9o0p1q2"
down_revision = "k6l7m8n9o0p1"
branch_labels = None
depends_on = None

# enum member name -> (A, B, C, Py/D)
DRAFT_PARAMS = {
    "MB_PLOUGH":   (652.0, 0.0, 5.1, 0.20),
    "DISC_PLOUGH": (124.0, 6.4, 0.0, 0.00),
    "DISC_HARROW": (254.0, 13.2, 0.0, 0.00),
    "CULTIVATOR":  (32.0, 1.9, 0.0, 0.20),
}


def upgrade() -> None:
    conn = op.get_bind()
    for member, (a, b, c, pyd) in DRAFT_PARAMS.items():
        conn.execute(
            sa.text(
                "UPDATE implements SET asae_param_a = :a, asae_param_b = :b, "
                "asae_param_c = :c, vertical_horizontal_ratio = :pyd "
                "WHERE is_library = true "
                "AND CAST(implement_type AS text) = :member"
            ),
            {"a": a, "b": b, "c": c, "pyd": pyd, "member": member},
        )


def downgrade() -> None:
    # Deliberately a no-op: the prior values were unsourced placeholders.
    pass

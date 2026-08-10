"""add active (PTO-powered) implement types, rotor spec columns and disc-harrow
configuration to implements

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-08-09

Notes
-----
`implement_type` is a NATIVE Postgres ENUM whose labels are the Python enum
MEMBER NAMES (the model omits `values_callable`), so the new labels below are
'ROTAVATOR' / 'DISC_HARROW_POWERED' / 'CULTIVATOR_POWERED', not their display
values. `ALTER TYPE ... ADD VALUE` cannot run inside a transaction block, hence
the autocommit block. Postgres also cannot REMOVE enum labels, so the downgrade
rebuilds the type from scratch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "i4j5k6l7m8n9"
down_revision = "h3i4j5k6l7m8"
branch_labels = None
depends_on = None

_NEW_IMPLEMENT_TYPE_LABELS = ("ROTAVATOR", "DISC_HARROW_POWERED", "CULTIVATOR_POWERED")
_ORIGINAL_IMPLEMENT_TYPE_LABELS = ("MB_PLOUGH", "DISC_PLOUGH", "CULTIVATOR", "DISC_HARROW")

_ROTOR_COLUMNS = (
    "rotor_mechanical_resistance",
    "rotor_efficiency",
    "rotor_pto_power",
    "rotor_speed",
    "rotor_dynamic_vertical_force",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # 1. Extend the native enum (Postgres only; SQLite renders it as VARCHAR).
    if _is_postgres():
        with op.get_context().autocommit_block():
            for label in _NEW_IMPLEMENT_TYPE_LABELS:
                op.execute(f"ALTER TYPE implement_type ADD VALUE IF NOT EXISTS '{label}'")

    # 2. Rotor specs for active/powered implements (all nullable -- passive
    #    implements simply leave them empty).
    for column_name in _ROTOR_COLUMNS:
        op.add_column("implements", sa.Column(column_name, sa.DECIMAL(), nullable=True))

    # 3. Descriptive disc-harrow arrangement. Purely informational: the DSS
    #    document gives no distinct coefficients for Tandem vs Offset, so this
    #    never enters any calculation.
    op.add_column("implements", sa.Column("configuration", sa.String(length=30), nullable=True))
    if _is_postgres():
        # SQLite cannot add a CHECK constraint to an existing table via ALTER,
        # and the SQLite dev path builds the schema from the model via
        # create_all() rather than from migrations, so this is Postgres-only.
        op.create_check_constraint(
            "ck_implements_configuration",
            "implements",
            "configuration IS NULL OR configuration IN ('Tandem','Offset')",
        )

    _seed_active_library_implements()


# Representative PLACEHOLDER catalogue data (mirrors app/utils/seed_library.py's
# ACTIVE_LIBRARY_IMPLEMENTS), inserted here because `seed_library_if_empty` only
# fires on a database with zero library implements -- existing installs would
# otherwise never receive the powered types. Matched by name, so re-running is a
# no-op. Values are illustrative, not manufacturer-published.
_ACTIVE_LIBRARY_ROWS = (
    # name, type label, width, weight, cg, Da, eta_r, P_PTO, N
    ("Rotavator (5 ft)", "ROTAVATOR", 1.5, 380, 0.45, 300, 0.30, 3.0, 540),
    ("Rotavator (7 ft)", "ROTAVATOR", 2.1, 520, 0.50, 420, 0.32, 4.5, 540),
    ("Powered Disc Harrow (20 Discs)", "DISC_HARROW_POWERED", 1.8, 410, 0.44, 350, 0.28, 3.5, 540),
    ("Powered Cultivator (11 Tines)", "CULTIVATOR_POWERED", 1.6, 330, 0.40, 280, 0.27, 3.0, 540),
)


def _seed_active_library_implements() -> None:
    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    insert = sa.text(
        """
        INSERT INTO implements (
            id, name, manufacturer, implement_type, width, weight,
            cg_distance_from_hitch, rotor_mechanical_resistance, rotor_efficiency,
            rotor_pto_power, rotor_speed, is_library, created_at, updated_at
        ) VALUES (
            :id, :name, :manufacturer, :implement_type, :width, :weight,
            :cg, :da, :eta_r, :pto_power, :rotor_speed, :is_library, :created_at, :updated_at
        )
        """
    )
    for name, type_label, width, weight, cg, da, eta_r, pto_power, rotor_speed in _ACTIVE_LIBRARY_ROWS:
        already_present = bind.execute(
            sa.text("SELECT 1 FROM implements WHERE name = :name AND is_library = :is_library"),
            {"name": name, "is_library": True},
        ).first()
        if already_present:
            continue
        bind.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "name": name,
                "manufacturer": "Standard",
                "implement_type": type_label,
                "width": width,
                "weight": weight,
                "cg": cg,
                "da": da,
                "eta_r": eta_r,
                "pto_power": pto_power,
                "rotor_speed": rotor_speed,
                "is_library": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    if _is_postgres():
        op.drop_constraint("ck_implements_configuration", "implements", type_="check")
    op.drop_column("implements", "configuration")
    for column_name in reversed(_ROTOR_COLUMNS):
        op.drop_column("implements", column_name)

    if not _is_postgres():
        return

    # Postgres cannot drop enum labels, so rebuild the type. Any row still using
    # a powered type would violate the narrowed type, so fail loudly rather than
    # silently discarding data.
    powered_rows = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM implements WHERE implement_type::text IN "
            "('ROTAVATOR','DISC_HARROW_POWERED','CULTIVATOR_POWERED')"
        )
    ).scalar()
    if powered_rows:
        raise RuntimeError(
            f"Cannot downgrade: {powered_rows} implement(s) still use a PTO-powered type. "
            "Delete or reclassify them first."
        )

    original = ", ".join(f"'{label}'" for label in _ORIGINAL_IMPLEMENT_TYPE_LABELS)
    op.execute("ALTER TYPE implement_type RENAME TO implement_type_old")
    op.execute(f"CREATE TYPE implement_type AS ENUM ({original})")
    op.execute(
        "ALTER TABLE implements ALTER COLUMN implement_type TYPE implement_type "
        "USING implement_type::text::implement_type"
    )
    op.execute("DROP TYPE implement_type_old")

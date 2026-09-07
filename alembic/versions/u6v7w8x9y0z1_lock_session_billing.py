"""lock the session charge at start and make it final at end

Adds the columns that let a session's charge be computed exactly once and never
re-derived:

* ``sessions.charge_unit`` / ``rate_currency`` / ``operation_charge_id`` -- the owner's
  rate, its unit and its provenance, snapshotted when the session starts, so a later edit
  to the rate card cannot retroactively re-bill a finished session.
* ``sessions.billable_hours`` -- worked hours net of pauses, for Threshing/Grading.
* ``sessions.cost_finalized_at`` -- the lock. Non-NULL means the charge is final.
* ``session_pauses`` -- the paused-interval ledger, used both to net pauses out of
  billable hours and to exclude GPS travelled during a pause from the worked area.

Money columns move from Float to Numeric so the rupee amount a farmer is billed is exact.

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "u6v7w8x9y0z1"
down_revision = "t5u6v7w8x9y0"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- Billing snapshot columns on sessions ------------------------------------
    op.add_column("sessions", sa.Column("charge_unit", sa.String(length=10), nullable=True))
    op.add_column(
        "sessions",
        sa.Column(
            "rate_currency",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'INR'"),
        ),
    )
    op.add_column("sessions", sa.Column("operation_charge_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("sessions", sa.Column("billable_hours", sa.Float(), nullable=True))
    op.add_column("sessions", sa.Column("cost_finalized_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_sessions_operation_charge_id",
        "sessions",
        "operation_charges",
        ["operation_charge_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_sessions_charge_unit",
        "sessions",
        "charge_unit IS NULL OR charge_unit IN ('per_ha','per_hour')",
    )

    # --- Pause ledger -------------------------------------------------------------
    op.create_table(
        "session_pauses",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            server_default=sa.text("gen_random_uuid()") if _is_postgres() else None,
            nullable=False,
        ),
        sa.Column("session_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_session_pauses_session_id", "session_pauses", ["session_id"])

    # --- Money columns: Float -> Numeric -------------------------------------------
    # Postgres needs an explicit USING for a float8 -> numeric cast.
    money_columns = (
        ("sessions", "total_cost_inr", sa.Numeric(12, 2)),
        ("sessions", "charge_per_ha_applied", sa.Numeric(12, 4)),
        ("operation_charges", "charge_per_ha", sa.Numeric(12, 4)),
        ("operation_charges", "charge_per_hour", sa.Numeric(12, 4)),
    )
    for table, column, target in money_columns:
        kwargs = {}
        if _is_postgres():
            kwargs["postgresql_using"] = f"{column}::numeric({target.precision},{target.scale})"
        op.alter_column(table, column, type_=target, existing_nullable=True, **kwargs)
    # charge_per_ha is NOT NULL; alter_column above defaulted existing_nullable=True, so
    # restate it to avoid dropping the constraint.
    op.alter_column("operation_charges", "charge_per_ha", nullable=False)

    # --- Freeze every bill that already exists ------------------------------------
    # Sessions that already carry a total keep exactly the amount they show today; they
    # are marked final so no read path recomputes them. Completed sessions with no total
    # stay unfinalized so the one-shot backfill in the report/area-summary routes can
    # close them from their own locked rate.
    op.execute(
        """
        UPDATE sessions
           SET cost_finalized_at = COALESCE(ended_at, updated_at, created_at),
               charge_unit = CASE
                   WHEN lower(operation_type) IN ('threshing','grading') THEN 'per_hour'
                   ELSE 'per_ha'
               END
         WHERE status IN ('completed','aborted')
           AND total_cost_inr IS NOT NULL
        """
    )
    # Legacy rows that never got a unit but do have a rate: record the unit so the
    # backfill and the UI label it correctly.
    op.execute(
        """
        UPDATE sessions
           SET charge_unit = CASE
                   WHEN lower(operation_type) IN ('threshing','grading') THEN 'per_hour'
                   ELSE 'per_ha'
               END
         WHERE charge_unit IS NULL
           AND charge_per_ha_applied IS NOT NULL
        """
    )


def downgrade() -> None:
    money_columns = (
        ("operation_charges", "charge_per_hour", sa.Float()),
        ("operation_charges", "charge_per_ha", sa.Float()),
        ("sessions", "charge_per_ha_applied", sa.Float()),
        ("sessions", "total_cost_inr", sa.Float()),
    )
    for table, column, target in money_columns:
        kwargs = {}
        if _is_postgres():
            kwargs["postgresql_using"] = f"{column}::double precision"
        op.alter_column(table, column, type_=target, existing_nullable=True, **kwargs)
    op.alter_column("operation_charges", "charge_per_ha", nullable=False)

    op.drop_index("ix_session_pauses_session_id", table_name="session_pauses")
    op.drop_table("session_pauses")

    op.drop_constraint("ck_sessions_charge_unit", "sessions", type_="check")
    op.drop_constraint("fk_sessions_operation_charge_id", "sessions", type_="foreignkey")
    op.drop_column("sessions", "cost_finalized_at")
    op.drop_column("sessions", "billable_hours")
    op.drop_column("sessions", "operation_charge_id")
    op.drop_column("sessions", "rate_currency")
    op.drop_column("sessions", "charge_unit")

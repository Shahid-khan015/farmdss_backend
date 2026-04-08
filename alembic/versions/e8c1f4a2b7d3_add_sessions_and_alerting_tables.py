"""add sessions, preset values, alerts, observations, and iot_readings session link

Revision ID: e8c1f4a2b7d3
Revises: d4e5f6a7b8c0
Create Date: 2026-03-31
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e8c1f4a2b7d3"
down_revision = "d4e5f6a7b8c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tractor_id", sa.Uuid(), nullable=False),
        sa.Column("implement_id", sa.Uuid(), nullable=True),
        sa.Column("operator_id", sa.Uuid(), nullable=False),
        sa.Column("tractor_owner_id", sa.Uuid(), nullable=True),
        sa.Column("client_farmer_id", sa.Uuid(), nullable=True),
        sa.Column("operation_type", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("gps_tracking_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("area_ha", sa.Float(), nullable=True),
        sa.Column("implement_width_m", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "operation_type IN ('Tillage','Sowing','Spraying','Weeding','Harvesting','Threshing','Grading')",
            name="ck_sessions_operation_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','paused','completed','aborted')",
            name="ck_sessions_status",
        ),
        sa.ForeignKeyConstraint(["tractor_id"], ["tractors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["implement_id"], ["implements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tractor_owner_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["client_farmer_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sessions_operator_id", "sessions", ["operator_id"], unique=False)
    op.create_index("idx_sessions_tractor_id", "sessions", ["tractor_id"], unique=False)
    op.create_index("idx_sessions_status", "sessions", ["status"], unique=False)
    op.create_index(
        "idx_sessions_started_at",
        "sessions",
        ["started_at"],
        unique=False,
        postgresql_ops={"started_at": "DESC"},
    )

    op.create_table(
        "session_preset_values",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("parameter_name", sa.String(length=50), nullable=False),
        sa.Column("required_value", sa.Float(), nullable=True),
        sa.Column("required_min", sa.Float(), nullable=True),
        sa.Column("required_max", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("deviation_pct_warn", sa.Float(), server_default=sa.text("10.0"), nullable=False),
        sa.Column("deviation_pct_crit", sa.Float(), server_default=sa.text("25.0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "parameter_name IN ('forward_speed','operation_depth','pto_shaft_speed','gearbox_temperature','wheel_slip','soil_moisture','field_capacity','vibration_level')",
            name="ck_session_preset_values_parameter_name",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "parameter_name", name="uq_session_preset_values_session_param"),
    )

    op.create_table(
        "iot_alerts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("reading_id", sa.Uuid(), nullable=True),
        sa.Column("feed_key", sa.String(length=50), nullable=False),
        sa.Column("alert_type", sa.String(length=20), nullable=False),
        sa.Column("alert_status", sa.String(length=20), nullable=False),
        sa.Column("actual_value", sa.Float(), nullable=True),
        sa.Column("reference_value", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("alert_type IN ('threshold','deviation')", name="ck_iot_alerts_alert_type"),
        sa.CheckConstraint("alert_status IN ('warning','critical')", name="ck_iot_alerts_alert_status"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reading_id"], ["iot_readings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_iot_alerts_session_id", "iot_alerts", ["session_id"], unique=False)
    op.create_index("idx_iot_alerts_acknowledged", "iot_alerts", ["acknowledged"], unique=False)
    op.create_index(
        "idx_iot_alerts_session_ack",
        "iot_alerts",
        ["session_id", "acknowledged"],
        unique=False,
    )

    op.create_table(
        "field_observations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("obs_type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(length=20), nullable=False),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("obs_type IN ('soil_moisture','cone_index')", name="ck_field_observations_obs_type"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_field_observations_session", "field_observations", ["session_id"], unique=False)

    iot_cols = {c["name"] for c in inspector.get_columns("iot_readings")}
    if "session_id" not in iot_cols:
        op.add_column("iot_readings", sa.Column("session_id", sa.Uuid(), nullable=True))

    fk_names = {fk.get("name") for fk in inspector.get_foreign_keys("iot_readings")}
    if "fk_iot_readings_session_id_sessions" not in fk_names:
        op.create_foreign_key(
            "fk_iot_readings_session_id_sessions",
            "iot_readings",
            "sessions",
            ["session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    iot_indexes = {ix.get("name") for ix in inspector.get_indexes("iot_readings")}
    if "idx_iot_readings_session_id" not in iot_indexes:
        op.create_index("idx_iot_readings_session_id", "iot_readings", ["session_id"], unique=False)
    if "idx_iot_readings_feed_session" not in iot_indexes:
        op.create_index(
            "idx_iot_readings_feed_session",
            "iot_readings",
            ["feed_key", "session_id", "device_timestamp"],
            unique=False,
            postgresql_ops={"device_timestamp": "DESC"},
        )


def downgrade() -> None:
    # 1) Drop indexes on iot_readings added above
    op.drop_index("idx_iot_readings_feed_session", table_name="iot_readings")
    op.drop_index("idx_iot_readings_session_id", table_name="iot_readings")

    # 2) Drop session_id column from iot_readings
    op.drop_constraint("fk_iot_readings_session_id_sessions", "iot_readings", type_="foreignkey")
    op.drop_column("iot_readings", "session_id")

    # 3) Drop field_observations
    op.drop_index("idx_field_observations_session", table_name="field_observations")
    op.drop_table("field_observations")

    # 4) Drop iot_alerts
    op.drop_index("idx_iot_alerts_session_ack", table_name="iot_alerts")
    op.drop_index("idx_iot_alerts_acknowledged", table_name="iot_alerts")
    op.drop_index("idx_iot_alerts_session_id", table_name="iot_alerts")
    op.drop_table("iot_alerts")

    # 5) Drop session_preset_values
    op.drop_table("session_preset_values")

    # 6) Drop sessions
    op.drop_index("idx_sessions_started_at", table_name="sessions")
    op.drop_index("idx_sessions_status", table_name="sessions")
    op.drop_index("idx_sessions_tractor_id", table_name="sessions")
    op.drop_index("idx_sessions_operator_id", table_name="sessions")
    op.drop_table("sessions")

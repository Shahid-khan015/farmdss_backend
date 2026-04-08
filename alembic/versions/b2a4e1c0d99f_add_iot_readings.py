"""add iot_readings time-series table

Revision ID: b2a4e1c0d99f
Revises: 7f3c2d8b9a11
Create Date: 2026-03-29 12:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "b2a4e1c0d99f"
down_revision = "7f3c2d8b9a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iot_readings",
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("feed_key", sa.String(length=64), nullable=False),
        sa.Column("raw_value", sa.Text(), nullable=False),
        sa.Column("numeric_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("device_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adafruit_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("adafruit_id", name="uq_iot_readings_adafruit_id"),
    )
    op.create_index("ix_iot_readings_device_id", "iot_readings", ["device_id"], unique=False)
    op.create_index("ix_iot_readings_feed_key", "iot_readings", ["feed_key"], unique=False)
    op.create_index("ix_iot_readings_device_timestamp", "iot_readings", ["device_timestamp"], unique=False)
    op.create_index(
        "ix_iot_readings_feed_key_device_ts",
        "iot_readings",
        ["feed_key", "device_timestamp"],
        unique=False,
    )
    op.create_index("ix_iot_readings_device_feed", "iot_readings", ["device_id", "feed_key"], unique=False)
    op.create_index("ix_iot_readings_session_id", "iot_readings", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_iot_readings_session_id", table_name="iot_readings")
    op.drop_index("ix_iot_readings_device_feed", table_name="iot_readings")
    op.drop_index("ix_iot_readings_feed_key_device_ts", table_name="iot_readings")
    op.drop_index("ix_iot_readings_device_timestamp", table_name="iot_readings")
    op.drop_index("ix_iot_readings_feed_key", table_name="iot_readings")
    op.drop_index("ix_iot_readings_device_id", table_name="iot_readings")
    op.drop_table("iot_readings")

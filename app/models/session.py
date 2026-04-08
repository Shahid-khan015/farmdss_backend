from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class OperationSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('Tillage','Sowing','Spraying','Weeding','Harvesting','Threshing','Grading')",
            name="ck_sessions_operation_type",
        ),
        CheckConstraint(
            "status IN ('active','paused','completed','aborted')",
            name="ck_sessions_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tractor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tractors.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    implement_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("implements.id", ondelete="SET NULL"),
        nullable=True,
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tractor_owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_farmer_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"), index=True)
    gps_tracking_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    area_ha: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    implement_width_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_cost_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    charge_per_ha_applied: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cost_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    tractor: Mapped["Tractor"] = relationship(
        "Tractor",
        back_populates="sessions",
        foreign_keys=[tractor_id],
    )
    implement: Mapped[Optional["Implement"]] = relationship(
        "Implement",
        back_populates="sessions",
        foreign_keys=[implement_id],
    )
    operator: Mapped["User"] = relationship(
        "User",
        back_populates="operated_sessions",
        foreign_keys=[operator_id],
    )
    owner: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="owned_sessions",
        foreign_keys=[tractor_owner_id],
    )
    farmer: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="client_sessions",
        foreign_keys=[client_farmer_id],
    )
    preset_values: Mapped[list["SessionPresetValue"]] = relationship(
        "SessionPresetValue",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    alerts: Mapped[list["IoTAlert"]] = relationship(
        "IoTAlert",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    field_observations: Mapped[list["FieldObservation"]] = relationship(
        "FieldObservation",
        back_populates="session",
        cascade="all, delete-orphan",
    )
    wage_record: Mapped[Optional["WageRecord"]] = relationship(
        "WageRecord",
        back_populates="session",
        uselist=False,
        foreign_keys="WageRecord.session_id",
    )
    fuel_logs: Mapped[list["FuelLog"]] = relationship(
        "FuelLog",
        back_populates="session",
        foreign_keys="FuelLog.session_id",
    )


class SessionPresetValue(Base):
    __tablename__ = "session_preset_values"
    __table_args__ = (
        CheckConstraint(
            "parameter_name IN ('forward_speed','operation_depth','pto_shaft_speed','gearbox_temperature','wheel_slip','soil_moisture','field_capacity','vibration_level')",
            name="ck_session_preset_values_parameter_name",
        ),
        UniqueConstraint("session_id", "parameter_name", name="uq_session_preset_values_session_param"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    parameter_name: Mapped[str] = mapped_column(String(50), nullable=False)
    required_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    required_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    required_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    deviation_pct_warn: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("10.0"))
    deviation_pct_crit: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("25.0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped["OperationSession"] = relationship("OperationSession", back_populates="preset_values")


class IoTAlert(Base):
    __tablename__ = "iot_alerts"
    __table_args__ = (
        CheckConstraint("alert_type IN ('threshold','deviation')", name="ck_iot_alerts_alert_type"),
        CheckConstraint("alert_status IN ('warning','critical')", name="ck_iot_alerts_alert_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reading_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("iot_readings.id", ondelete="SET NULL"),
        nullable=True,
    )
    feed_key: Mapped[str] = mapped_column(String(50), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(20), nullable=False)
    alert_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actual_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reference_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), index=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped[Optional["OperationSession"]] = relationship("OperationSession", back_populates="alerts")


class FieldObservation(Base):
    __tablename__ = "field_observations"
    __table_args__ = (
        CheckConstraint("obs_type IN ('soil_moisture','cone_index')", name="ck_field_observations_obs_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    obs_type: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    recorded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    session: Mapped["OperationSession"] = relationship("OperationSession", back_populates="field_observations")


class WageRecord(Base):
    __tablename__ = "wage_records"
    __table_args__ = (
        CheckConstraint(
            "rate_type IN ('hourly','per_ha')",
            name="ck_wage_records_rate_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    rate_type: Mapped[str] = mapped_column(String(10), nullable=False)
    rate_amount: Mapped[float] = mapped_column(Float, nullable=False)
    area_ha: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    disputed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    dispute_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    session: Mapped["OperationSession"] = relationship(
        "OperationSession",
        back_populates="wage_record",
        foreign_keys=[session_id],
    )
    operator: Mapped["User"] = relationship("User", foreign_keys=[operator_id])
    approver: Mapped[Optional["User"]] = relationship("User", foreign_keys=[approved_by])


class FuelLog(Base):
    __tablename__ = "fuel_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tractor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tractors.id"),
        nullable=False,
        index=True,
    )
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    litres: Mapped[float] = mapped_column(Float, nullable=False)
    refilled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cost_per_litre: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entered_by: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    tractor: Mapped["Tractor"] = relationship("Tractor", foreign_keys=[tractor_id])
    session: Mapped[Optional["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="fuel_logs",
        foreign_keys=[session_id],
    )
    entered_by_user: Mapped["User"] = relationship("User", foreign_keys=[entered_by])

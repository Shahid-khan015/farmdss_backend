from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import BOOLEAN, DECIMAL, Column, Enum, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import DriveMode
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Tractor(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tractors"

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    model: Mapped[str] = mapped_column(String, nullable=False, index=True)

    pto_power: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kW
    rated_engine_speed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # rpm
    max_engine_torque: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # N-m
    pto_rpm_min = Column(Integer, nullable=True)
    pto_rpm_max = Column(Integer, nullable=True)
    tow_capacity_kg = Column(Float, nullable=True)
    hitch_type = Column(String(30), nullable=True)

    wheelbase: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    front_axle_weight: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    rear_axle_weight: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    hitch_distance_from_rear: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    cg_distance_from_rear: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    rear_wheel_rolling_radius: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m

    drive_mode: Mapped[DriveMode] = mapped_column(
        Enum(DriveMode, name="drive_mode"),
        nullable=False,
        index=True,
        default=DriveMode.WD2,
    )
    transmission_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    power_reserve: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %

    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_library: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, default=False, index=True)

    tire_specification: Mapped[Optional["TireSpecification"]] = relationship(
        back_populates="tractor", uselist=False, cascade="all, delete-orphan"
    )
    simulations: Mapped[list["Simulation"]] = relationship(
        back_populates="tractor", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="tractor",
        foreign_keys="OperationSession.tractor_id",
    )


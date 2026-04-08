from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, JSON, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Simulation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "simulations"

    name: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)

    tractor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tractors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    implement_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("implements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    operating_conditions_preset_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operating_conditions_presets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Custom operating conditions (if preset not used):
    cone_index: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kPa
    depth: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # cm
    speed: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # km/h
    field_area: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # hectares
    field_length: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    field_width: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    number_of_turns: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    soil_texture: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    soil_hardness: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Results
    results: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    draft_force: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # N
    drawbar_power: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kW
    slip: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    traction_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    power_utilization: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    field_capacity_theoretical: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # ha/h
    field_capacity_actual: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # ha/h
    field_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    fuel_consumption_per_hectare: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # l/ha
    overall_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # %
    ballast_front_required: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    ballast_rear_required: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommendations: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    tractor: Mapped["Tractor"] = relationship(back_populates="simulations")
    implement: Mapped["Implement"] = relationship(back_populates="simulations")
    preset: Mapped[Optional["OperatingConditionPreset"]] = relationship(
        back_populates="simulations"
    )


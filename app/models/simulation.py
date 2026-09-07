from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, JSON, Enum, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import SimulationCombinationType
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
    # Single mode: the implement. Passive-passive mode: tool 1 (leading tool).
    # Active-passive mode: the passive (towed) tool; the rotor is not a catalog
    # Implement (see rotor_* columns below) since the DSS document specifies it
    # via ad hoc numeric specs (torque/speed/efficiency), not ASAE draft params.
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

    combination_type: Mapped[SimulationCombinationType] = mapped_column(
        # `values_callable` persists the enum *values* ("single", "passive_passive",
        # "active_passive") rather than SQLAlchemy's default of member *names*.
        # The migration creates the Postgres type from the values, the API schema
        # validates against the values, and `server_default` below is a value — so
        # without this the ORM writes "SINGLE" into a type that only accepts "single".
        Enum(
            SimulationCombinationType,
            name="simulation_combination_type",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=SimulationCombinationType.SINGLE,
        server_default=SimulationCombinationType.SINGLE.value,
    )

    # --- Passive-passive combination (DSS Section 4) ---
    implement_2_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("implements.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    interaction_coefficient: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # ki, 0.00-0.25
    #: Field-capacity swath width override, m. None (the default) preserves the
    #: engine's own max(width_1, width_2) -- see combi_algorithms.py.
    effective_width_override_m: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)

    # --- Active-passive combination (DSS Section 5): PTO-driven rotor specs ---
    rotor_weight: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    rotor_cg_distance_from_hitch: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    rotor_mechanical_resistance: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # Da, N
    rotor_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # eta_r, 0.25-0.45
    rotor_pto_power: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # P_PTO, kW
    rotor_speed: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # N, rpm
    rotor_dynamic_vertical_force: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # Fv, N

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
    implement: Mapped["Implement"] = relationship(back_populates="simulations", foreign_keys=[implement_id])
    implement_2: Mapped[Optional["Implement"]] = relationship(foreign_keys=[implement_2_id])
    preset: Mapped[Optional["OperatingConditionPreset"]] = relationship(
        back_populates="simulations"
    )


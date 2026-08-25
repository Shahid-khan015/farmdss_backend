from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import BOOLEAN, DECIMAL, Column, Enum, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import ImplementType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Implement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "implements"

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    manufacturer: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    implement_type: Mapped[ImplementType] = mapped_column(
        Enum(ImplementType, name="implement_type"),
        nullable=False,
        index=True,
    )

    width: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    weight: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kg
    cg_distance_from_hitch: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    vertical_horizontal_ratio: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)

    asae_param_a: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)
    asae_param_b: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)
    asae_param_c: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)

    # ASABE D497 tabulates some implement classes per tool rather than per metre
    # of width, so Eq. 3.1's `W` is the tool count for those. Populated for
    # cultivators; ignored for full-width tools. See
    # `app.core.constants.DRAFT_WIDTH_IS_TOOL_COUNT`.
    number_of_tools: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    working_width_m = Column(Float, nullable=True)
    hitch_type = Column(String(30), nullable=True)
    preset_speed_kmh = Column(Float, nullable=True)
    preset_depth_cm = Column(Float, nullable=True)
    preset_gearbox_temp_max_c = Column(Float, nullable=True)

    # Descriptive disc-harrow arrangement ('Tandem' / 'Offset'). Purely
    # informational: the DSS document gives no distinct coefficients for these,
    # so this never affects the calculations.
    configuration: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # --- Active (PTO-powered) rotor specs -------------------------------------
    # Populated only for ACTIVE implement types (Rotavator / powered variants).
    # These feed ActiveRotorInputs when the implement is selected as the rotor of
    # an active-passive combination; `weight` and `cg_distance_from_hitch` above
    # are reused for the rotor's mass properties. Requests may still supply
    # rotor_* inline to override any of these per simulation.
    rotor_mechanical_resistance: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # Da, N
    rotor_efficiency: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # eta_r, 0.25-0.45
    rotor_pto_power: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # P_PTO, kW
    rotor_speed: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # N, rpm
    rotor_dynamic_vertical_force: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # Fv, N

    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_library: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, default=False, index=True)

    simulations: Mapped[list["Simulation"]] = relationship(
        back_populates="implement",
        foreign_keys="Simulation.implement_id",
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="implement",
        foreign_keys="OperationSession.implement_id",
    )

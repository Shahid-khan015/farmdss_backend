from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import BOOLEAN, DECIMAL, Column, Enum, Float, ForeignKey, String, Uuid
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
    working_width_m = Column(Float, nullable=True)
    hitch_type = Column(String(30), nullable=True)
    preset_speed_kmh = Column(Float, nullable=True)
    preset_depth_cm = Column(Float, nullable=True)
    preset_gearbox_temp_max_c = Column(Float, nullable=True)

    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_library: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, default=False, index=True)

    simulations: Mapped[list["Simulation"]] = relationship(
        back_populates="implement", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["OperationSession"]] = relationship(
        "OperationSession",
        back_populates="implement",
        foreign_keys="OperationSession.implement_id",
    )

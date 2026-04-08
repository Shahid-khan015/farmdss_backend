from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import TireType
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class TireSpecification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tire_specifications"

    tractor_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tractors.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    tire_type: Mapped[TireType] = mapped_column(
        Enum(TireType, name="tire_type"), nullable=False
    )

    front_overall_diameter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    front_section_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    front_static_loaded_radius: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    front_rolling_radius: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm

    rear_overall_diameter: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    rear_section_width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    rear_static_loaded_radius: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm
    rear_rolling_radius: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # mm

    tractor: Mapped["Tractor"] = relationship(back_populates="tire_specification")


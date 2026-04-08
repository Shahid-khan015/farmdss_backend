from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import SoilHardness, SoilTexture
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OperatingConditionPreset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "operating_conditions_presets"

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)

    soil_texture: Mapped[SoilTexture] = mapped_column(
        Enum(SoilTexture, name="soil_texture"),
        nullable=False,
        index=True,
    )
    soil_hardness: Mapped[SoilHardness] = mapped_column(
        Enum(SoilHardness, name="soil_hardness"),
        nullable=False,
        index=True,
    )

    cone_index: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # kPa
    depth: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # cm
    speed: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # km/h

    field_area: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # hectares
    field_length: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    field_width: Mapped[Optional[Decimal]] = mapped_column(DECIMAL, nullable=True)  # m
    number_of_turns: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    simulations: Mapped[list["Simulation"]] = relationship(back_populates="preset")


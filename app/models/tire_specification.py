from __future__ import annotations

import uuid
from typing import Optional

from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String, Uuid
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

    # Tyre size designation, e.g. "13.6 x 28". Not used by the engine, but the
    # only field the overall diameter can be re-derived from or checked against
    # -- its absence is why rim diameters sat in the overall-diameter columns
    # unnoticed. Section width = first number (in), rim diameter = second (in).
    front_tire_size: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    rear_tire_size: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Millimetres, to 2 dp. These were INTEGER, which truncated the fractional
    # millimetres real tyre specs carry (a 12.4 x 28 is 1226.31 mm, not 1226).
    # `Bn = CI*b*d/Wd` is linear in both `b` and `d`, so the truncations compound
    # through the whole traction chain -- measurably, ~0.6% on Bn and ~0.9% on the
    # front axle load. Numeric(7, 2) holds the published values exactly.
    front_overall_diameter: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    front_section_width: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    front_static_loaded_radius: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    front_rolling_radius: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm

    rear_overall_diameter: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    rear_section_width: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    rear_static_loaded_radius: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm
    rear_rolling_radius: Mapped[Optional[Decimal]] = mapped_column(Numeric(7, 2), nullable=True)  # mm

    tractor: Mapped["Tractor"] = relationship(back_populates="tire_specification")


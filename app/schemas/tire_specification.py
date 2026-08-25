from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TireType
from app.schemas.common import Timestamped, UUIDResponse


class TireSpecificationBase(BaseModel):
    tire_type: TireType

    front_tire_size: Optional[str] = Field(default=None, max_length=30)
    rear_tire_size: Optional[str] = Field(default=None, max_length=30)
    front_overall_diameter: Optional[Decimal] = Field(default=None, ge=0)
    front_section_width: Optional[Decimal] = Field(default=None, ge=0)
    front_static_loaded_radius: Optional[Decimal] = Field(default=None, ge=0)
    front_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)

    rear_overall_diameter: Optional[Decimal] = Field(default=None, ge=0)
    rear_section_width: Optional[Decimal] = Field(default=None, ge=0)
    rear_static_loaded_radius: Optional[Decimal] = Field(default=None, ge=0)
    rear_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)


class TireSpecificationCreate(TireSpecificationBase):
    pass


class TireSpecificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tire_type: Optional[TireType] = None

    front_tire_size: Optional[str] = Field(default=None, max_length=30)
    rear_tire_size: Optional[str] = Field(default=None, max_length=30)
    front_overall_diameter: Optional[Decimal] = Field(default=None, ge=0)
    front_section_width: Optional[Decimal] = Field(default=None, ge=0)
    front_static_loaded_radius: Optional[Decimal] = Field(default=None, ge=0)
    front_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)

    rear_overall_diameter: Optional[Decimal] = Field(default=None, ge=0)
    rear_section_width: Optional[Decimal] = Field(default=None, ge=0)
    rear_static_loaded_radius: Optional[Decimal] = Field(default=None, ge=0)
    rear_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)


class TireSpecificationRead(UUIDResponse, Timestamped, TireSpecificationBase):
    tractor_id: uuid.UUID


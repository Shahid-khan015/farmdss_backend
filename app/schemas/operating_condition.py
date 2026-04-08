from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SoilHardness, SoilTexture
from app.schemas.common import Timestamped, UUIDResponse


class OperatingConditionBase(BaseModel):
    name: str = Field(min_length=1)
    soil_texture: SoilTexture
    soil_hardness: SoilHardness

    cone_index: Optional[Decimal] = Field(default=None, ge=0)  # kPa
    depth: Optional[Decimal] = Field(default=None, ge=0)  # cm
    speed: Optional[Decimal] = Field(default=None, ge=0)  # km/h
    field_area: Optional[Decimal] = Field(default=None, ge=0)  # hectares
    field_length: Optional[Decimal] = Field(default=None, ge=0)  # m
    field_width: Optional[Decimal] = Field(default=None, ge=0)  # m
    number_of_turns: Optional[int] = Field(default=None, ge=0)


class OperatingConditionCreate(OperatingConditionBase):
    pass


class OperatingConditionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1)
    soil_texture: Optional[SoilTexture] = None
    soil_hardness: Optional[SoilHardness] = None

    cone_index: Optional[Decimal] = Field(default=None, ge=0)
    depth: Optional[Decimal] = Field(default=None, ge=0)
    speed: Optional[Decimal] = Field(default=None, ge=0)
    field_area: Optional[Decimal] = Field(default=None, ge=0)
    field_length: Optional[Decimal] = Field(default=None, ge=0)
    field_width: Optional[Decimal] = Field(default=None, ge=0)
    number_of_turns: Optional[int] = Field(default=None, ge=0)


class OperatingConditionRead(UUIDResponse, Timestamped, OperatingConditionBase):
    model_config = ConfigDict(from_attributes=True)


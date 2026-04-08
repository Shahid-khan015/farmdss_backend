from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ImplementType
from app.schemas.common import Timestamped, UUIDResponse


class ImplementBase(BaseModel):
    name: str = Field(min_length=1)
    manufacturer: Optional[str] = None
    implement_type: ImplementType

    width: Optional[Decimal] = Field(default=None, ge=0)
    weight: Optional[Decimal] = Field(default=None, ge=0)
    cg_distance_from_hitch: Optional[Decimal] = Field(default=None, ge=0)
    vertical_horizontal_ratio: Optional[Decimal] = Field(default=None, ge=0)

    asae_param_a: Optional[Decimal] = Field(default=None)
    asae_param_b: Optional[Decimal] = Field(default=None)
    asae_param_c: Optional[Decimal] = Field(default=None)
    working_width_m: Optional[float] = None
    hitch_type: Optional[str] = None
    preset_speed_kmh: Optional[float] = None
    preset_depth_cm: Optional[float] = None
    preset_gearbox_temp_max_c: Optional[float] = None

    is_library: bool = False


class ImplementCreate(ImplementBase):
    pass


class ImplementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1)
    manufacturer: Optional[str] = None
    implement_type: Optional[ImplementType] = None

    width: Optional[Decimal] = Field(default=None, ge=0)
    weight: Optional[Decimal] = Field(default=None, ge=0)
    cg_distance_from_hitch: Optional[Decimal] = Field(default=None, ge=0)
    vertical_horizontal_ratio: Optional[Decimal] = Field(default=None, ge=0)

    asae_param_a: Optional[Decimal] = None
    asae_param_b: Optional[Decimal] = None
    asae_param_c: Optional[Decimal] = None
    working_width_m: Optional[float] = None
    hitch_type: Optional[str] = None
    preset_speed_kmh: Optional[float] = None
    preset_depth_cm: Optional[float] = None
    preset_gearbox_temp_max_c: Optional[float] = None


class ImplementRead(UUIDResponse, Timestamped, ImplementBase):
    model_config = ConfigDict(from_attributes=True)

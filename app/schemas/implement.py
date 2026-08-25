from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DiscHarrowConfiguration, ImplementType
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
    number_of_tools: Optional[int] = Field(default=None, gt=0)
    working_width_m: Optional[float] = None
    hitch_type: Optional[str] = None
    preset_speed_kmh: Optional[float] = None
    preset_depth_cm: Optional[float] = None
    preset_gearbox_temp_max_c: Optional[float] = None

    # Descriptive only -- no effect on any calculation.
    configuration: Optional[DiscHarrowConfiguration] = None

    # Rotor specs, for ACTIVE (PTO-powered) implement types only.
    rotor_mechanical_resistance: Optional[Decimal] = Field(default=None, ge=0)
    rotor_efficiency: Optional[Decimal] = Field(default=None, ge=0.25, le=0.45)
    rotor_pto_power: Optional[Decimal] = Field(default=None, gt=0)
    rotor_speed: Optional[Decimal] = Field(default=None, gt=0)
    rotor_dynamic_vertical_force: Optional[Decimal] = Field(default=None)

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
    number_of_tools: Optional[int] = None
    working_width_m: Optional[float] = None
    hitch_type: Optional[str] = None
    preset_speed_kmh: Optional[float] = None
    preset_depth_cm: Optional[float] = None
    preset_gearbox_temp_max_c: Optional[float] = None

    configuration: Optional[DiscHarrowConfiguration] = None

    rotor_mechanical_resistance: Optional[Decimal] = Field(default=None, ge=0)
    rotor_efficiency: Optional[Decimal] = Field(default=None, ge=0.25, le=0.45)
    rotor_pto_power: Optional[Decimal] = Field(default=None, gt=0)
    rotor_speed: Optional[Decimal] = Field(default=None, gt=0)
    rotor_dynamic_vertical_force: Optional[Decimal] = None


class ImplementRead(UUIDResponse, Timestamped, ImplementBase):
    model_config = ConfigDict(from_attributes=True)

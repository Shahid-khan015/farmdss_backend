from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DriveMode
from app.schemas.common import Timestamped, UUIDResponse
from app.schemas.tire_specification import TireSpecificationCreate, TireSpecificationRead


class TractorBase(BaseModel):
    name: str = Field(min_length=1)
    manufacturer: Optional[str] = None
    model: str = Field(min_length=1)

    pto_power: Optional[Decimal] = Field(default=None, ge=0)
    rated_engine_speed: Optional[int] = Field(default=None, ge=0)
    max_engine_torque: Optional[Decimal] = Field(default=None, ge=0)
    pto_rpm_min: Optional[int] = None
    pto_rpm_max: Optional[int] = None
    tow_capacity_kg: Optional[float] = None
    hitch_type: Optional[str] = None

    wheelbase: Optional[Decimal] = Field(default=None, ge=0)
    front_axle_weight: Optional[Decimal] = Field(default=None, ge=0)
    rear_axle_weight: Optional[Decimal] = Field(default=None, ge=0)
    hitch_distance_from_rear: Optional[Decimal] = Field(default=None, ge=0)
    cg_distance_from_rear: Optional[Decimal] = Field(default=None, ge=0)
    rear_wheel_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)

    drive_mode: DriveMode = DriveMode.WD2
    transmission_efficiency: Optional[Decimal] = Field(default=None, ge=0)
    power_reserve: Optional[Decimal] = Field(default=None, ge=0)

    is_library: bool = False


class TractorCreate(TractorBase):
    tire_specification: Optional[TireSpecificationCreate] = None


class TractorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1)
    manufacturer: Optional[str] = None
    model: Optional[str] = Field(default=None, min_length=1)

    pto_power: Optional[Decimal] = Field(default=None, ge=0)
    rated_engine_speed: Optional[int] = Field(default=None, ge=0)
    max_engine_torque: Optional[Decimal] = Field(default=None, ge=0)
    pto_rpm_min: Optional[int] = None
    pto_rpm_max: Optional[int] = None
    tow_capacity_kg: Optional[float] = None
    hitch_type: Optional[str] = None

    wheelbase: Optional[Decimal] = Field(default=None, ge=0)
    front_axle_weight: Optional[Decimal] = Field(default=None, ge=0)
    rear_axle_weight: Optional[Decimal] = Field(default=None, ge=0)
    hitch_distance_from_rear: Optional[Decimal] = Field(default=None, ge=0)
    cg_distance_from_rear: Optional[Decimal] = Field(default=None, ge=0)
    rear_wheel_rolling_radius: Optional[Decimal] = Field(default=None, ge=0)

    drive_mode: Optional[DriveMode] = None
    transmission_efficiency: Optional[Decimal] = Field(default=None, ge=0)
    power_reserve: Optional[Decimal] = Field(default=None, ge=0)


class TractorRead(UUIDResponse, Timestamped, TractorBase):
    model_config = ConfigDict(from_attributes=True)

    tire_specification: Optional[TireSpecificationRead] = None


from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Timestamped, UUIDResponse


class SimulationRunRequest(BaseModel):
    """
    Run and persist a simulation.

    If operating_conditions_preset_id is provided, operating conditions are taken from that preset.
    Otherwise, custom operating conditions must be provided.
    """

    name: Optional[str] = None
    tractor_id: uuid.UUID
    implement_id: uuid.UUID
    operating_conditions_preset_id: Optional[uuid.UUID] = None

    # Custom conditions
    cone_index: Optional[Decimal] = Field(default=None, ge=0)
    depth: Optional[Decimal] = Field(default=None, ge=0)
    speed: Optional[Decimal] = Field(default=None, ge=0)
    field_area: Optional[Decimal] = Field(default=None, ge=0)
    field_length: Optional[Decimal] = Field(default=None, ge=0)
    field_width: Optional[Decimal] = Field(default=None, ge=0)
    number_of_turns: Optional[int] = Field(default=None, ge=0)
    soil_texture: Optional[str] = None
    soil_hardness: Optional[str] = None

    @model_validator(mode="after")
    def validate_preset_or_custom(self) -> "SimulationRunRequest":
        if self.operating_conditions_preset_id is not None:
            return self
        required = [
            ("cone_index", self.cone_index),
            ("depth", self.depth),
            ("speed", self.speed),
            ("field_area", self.field_area),
            ("field_length", self.field_length),
            ("field_width", self.field_width),
        ]
        missing = [k for k, v in required if v is None]
        if missing:
            raise ValueError(
                f"Custom operating conditions required when no preset is used. Missing: {missing}"
            )
        return self


class SimulationRead(UUIDResponse, Timestamped, BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = None
    tractor_id: uuid.UUID
    implement_id: uuid.UUID
    operating_conditions_preset_id: Optional[uuid.UUID] = None

    cone_index: Optional[Decimal] = None
    depth: Optional[Decimal] = None
    speed: Optional[Decimal] = None
    field_area: Optional[Decimal] = None
    field_length: Optional[Decimal] = None
    field_width: Optional[Decimal] = None
    number_of_turns: Optional[int] = None
    soil_texture: Optional[str] = None
    soil_hardness: Optional[str] = None

    results: Optional[dict[str, Any]] = None

    draft_force: Optional[Decimal] = None
    drawbar_power: Optional[Decimal] = None
    slip: Optional[Decimal] = None
    traction_efficiency: Optional[Decimal] = None
    power_utilization: Optional[Decimal] = None
    field_capacity_theoretical: Optional[Decimal] = None
    field_capacity_actual: Optional[Decimal] = None
    field_efficiency: Optional[Decimal] = None
    fuel_consumption_per_hectare: Optional[Decimal] = None
    overall_efficiency: Optional[Decimal] = None
    ballast_front_required: Optional[Decimal] = None
    ballast_rear_required: Optional[Decimal] = None
    status_message: Optional[str] = None
    recommendations: Optional[str] = None


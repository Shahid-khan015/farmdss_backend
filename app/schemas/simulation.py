from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import SimulationCombinationType
from app.schemas.common import Timestamped, UUIDResponse


class SimulationRunRequest(BaseModel):
    """
    Run and persist a simulation.

    If operating_conditions_preset_id is provided, operating conditions are taken from that preset.
    Otherwise, custom operating conditions must be provided.

    combination_type selects which DSS simulation mode to run:
      - "single" (default): implement_id is the one implement used.
      - "passive_passive": implement_id is tool 1, implement_2_id is tool 2 (both
        towed/passive), interaction_coefficient (ki, 0.00-0.25) is required.
      - "active_passive": implement_id is the towed passive tool, and the rotor_*
        fields describe the PTO-driven active rotor unit.
    """

    name: Optional[str] = None
    tractor_id: uuid.UUID
    implement_id: uuid.UUID
    operating_conditions_preset_id: Optional[uuid.UUID] = None

    combination_type: SimulationCombinationType = SimulationCombinationType.SINGLE
    implement_2_id: Optional[uuid.UUID] = None
    interaction_coefficient: Optional[Decimal] = Field(default=None, ge=0, le=0.25)
    #: Passive-passive field-capacity swath width, m. Optional; when omitted
    #: (the default) the engine uses max(width_1, width_2), matching both
    #: reference HTML tools' own default. Only meaningful for
    #: combination_type=passive_passive.
    effective_width_override_m: Optional[Decimal] = Field(default=None, gt=0)

    rotor_weight: Optional[Decimal] = Field(default=None, gt=0)
    rotor_cg_distance_from_hitch: Optional[Decimal] = Field(default=None, ge=0)
    rotor_mechanical_resistance: Optional[Decimal] = Field(default=None, ge=0)
    rotor_efficiency: Optional[Decimal] = Field(default=None, ge=0.25, le=0.45)
    rotor_pto_power: Optional[Decimal] = Field(default=None, gt=0)
    rotor_speed: Optional[Decimal] = Field(default=None, gt=0)
    rotor_dynamic_vertical_force: Optional[Decimal] = Field(default=None)

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
        if self.operating_conditions_preset_id is None:
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

        # combination_type is already constrained by the enum itself; no string
        # allow-list is duplicated here (an extra literal list would silently
        # reject any future enum member).
        if self.combination_type == SimulationCombinationType.PASSIVE_PASSIVE:
            if self.implement_2_id is None:
                raise ValueError("implement_2_id is required for combination_type=passive_passive")
            if self.interaction_coefficient is None:
                raise ValueError(
                    "interaction_coefficient (ki, 0.00-0.25) is required for combination_type=passive_passive"
                )

        if self.combination_type == SimulationCombinationType.ACTIVE_PASSIVE:
            # The rotor may be selected from the implement catalogue via
            # implement_2_id, in which case its specs are resolved server-side
            # and any inline rotor_* value simply overrides that record's value.
            # Inline values remain REQUIRED when no catalogue rotor is chosen,
            # preserving the original API contract.
            if self.implement_2_id is None:
                required_rotor = [
                    ("rotor_weight", self.rotor_weight),
                    ("rotor_cg_distance_from_hitch", self.rotor_cg_distance_from_hitch),
                    ("rotor_mechanical_resistance", self.rotor_mechanical_resistance),
                    ("rotor_efficiency", self.rotor_efficiency),
                    ("rotor_pto_power", self.rotor_pto_power),
                    ("rotor_speed", self.rotor_speed),
                ]
                missing_rotor = [k for k, v in required_rotor if v is None]
                if missing_rotor:
                    raise ValueError(
                        "Rotor fields required for combination_type=active_passive when no rotor "
                        f"implement (implement_2_id) is selected. Missing: {missing_rotor}"
                    )

        return self


class SimulationRead(UUIDResponse, Timestamped, BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: Optional[str] = None
    tractor_id: uuid.UUID
    implement_id: uuid.UUID
    operating_conditions_preset_id: Optional[uuid.UUID] = None

    combination_type: SimulationCombinationType = SimulationCombinationType.SINGLE
    implement_2_id: Optional[uuid.UUID] = None
    interaction_coefficient: Optional[Decimal] = None
    effective_width_override_m: Optional[Decimal] = None
    rotor_weight: Optional[Decimal] = None
    rotor_cg_distance_from_hitch: Optional[Decimal] = None
    rotor_mechanical_resistance: Optional[Decimal] = None
    rotor_efficiency: Optional[Decimal] = None
    rotor_pto_power: Optional[Decimal] = None
    rotor_speed: Optional[Decimal] = None
    rotor_dynamic_vertical_force: Optional[Decimal] = None

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
    status: Optional[str] = None
    warnings: Optional[list[str]] = None
    confidence: Optional[str] = None
    recommendation_messages: Optional[list[str]] = None

    @model_validator(mode="after")
    def hydrate_engineering_metadata(self) -> "SimulationRead":
        if self.results:
            self.status = self.status or self.results.get("status")
            self.warnings = self.warnings or self.results.get("warnings")
            self.confidence = self.confidence or self.results.get("confidence")
            self.recommendation_messages = (
                self.recommendation_messages or self.results.get("recommendation_messages")
            )
        return self


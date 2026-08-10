from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_PARAMETER_NAMES: tuple[str, ...] = (
    "forward_speed",
    "operation_depth",
    "pto_shaft_speed",
    "gearbox_temperature",
    "wheel_slip",
    "soil_moisture",
    "field_capacity",
    "vibration_level",
)

ALLOWED_OPERATION_TYPES: tuple[str, ...] = (
    "Tillage",
    "Sowing",
    "Spraying",
    "Weeding",
    "Harvesting",
    "Threshing",
    "Grading",
)

ALLOWED_OBS_TYPES: tuple[str, ...] = ("soil_moisture", "cone_index")


class PresetValueCreate(BaseModel):
    parameter_name: str
    required_value: Optional[float] = None
    required_min: Optional[float] = None
    required_max: Optional[float] = None
    unit: str
    deviation_pct_warn: float = 10.0
    deviation_pct_crit: float = 25.0

    @field_validator("parameter_name")
    @classmethod
    def validate_parameter_name(cls, v: str) -> str:
        if v not in ALLOWED_PARAMETER_NAMES:
            raise ValueError(f"parameter_name must be one of: {', '.join(ALLOWED_PARAMETER_NAMES)}")
        return v


class SessionStartRequest(BaseModel):
    tractor_id: str
    implement_id: Optional[str] = None
    operation_type: str
    client_farmer_id: Optional[str] = None
    gps_tracking_enabled: bool = True
    preset_values: list[PresetValueCreate] = []  # DEPRECATED: operator-submitted presets are ignored.

    @field_validator("operation_type")
    @classmethod
    def validate_operation_type(cls, v: str) -> str:
        if v not in ALLOWED_OPERATION_TYPES:
            raise ValueError(f"operation_type must be one of: {', '.join(ALLOWED_OPERATION_TYPES)}")
        return v


class SessionStopRequest(BaseModel):
    notes: Optional[str] = None


class FieldObservationCreate(BaseModel):
    obs_type: str
    value: float
    unit: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    notes: Optional[str] = None

    @field_validator("obs_type")
    @classmethod
    def validate_obs_type(cls, v: str) -> str:
        if v not in ALLOWED_OBS_TYPES:
            raise ValueError(f"obs_type must be one of: {', '.join(ALLOWED_OBS_TYPES)}")
        return v


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: Optional[uuid.UUID]
    feed_key: str
    alert_type: str
    alert_status: str
    actual_value: Optional[float]
    reference_value: Optional[float]
    message: str
    acknowledged: bool
    acknowledged_at: Optional[datetime]
    created_at: datetime


class AlertListResponse(BaseModel):
    total: int
    items: list[AlertResponse]


class AlertSummaryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    feed_key: str
    alert_type: str
    alert_status: str
    severity_color: Optional[str] = None
    actual_value: Optional[float]
    message: str
    acknowledged: bool
    created_at: datetime


class SessionMetricSummary(BaseModel):
    feed_key: str
    label: str
    unit: str
    samples: int
    last_value: Optional[float] = None
    avg_value: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None


class PresetSummaryItem(BaseModel):
    parameter_name: str
    target_value: Optional[float] = None
    actual_value: Optional[float] = None
    unit: str
    deviation_pct: Optional[float] = None
    status: str


class PresetValueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    parameter_name: str
    required_value: Optional[float]
    required_min: Optional[float]
    required_max: Optional[float]
    unit: str
    deviation_pct_warn: float
    deviation_pct_crit: float


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tractor_id: uuid.UUID
    tractor_name: Optional[str] = None
    implement_id: Optional[uuid.UUID]
    operator_id: uuid.UUID
    operator_name: Optional[str] = None
    tractor_owner_id: Optional[uuid.UUID]
    client_farmer_id: Optional[uuid.UUID]
    operation_type: str
    started_at: datetime
    ended_at: Optional[datetime]
    status: str
    gps_tracking_enabled: bool
    area_ha: Optional[float]
    implement_width_m: Optional[float]
    alerts_count: Optional[int] = None
    unacknowledged_alerts: Optional[int] = None
    total_cost_inr: Optional[float] = None
    charge_per_ha_applied: Optional[float] = None
    cost_note: Optional[str] = None
    created_at: datetime


class SessionDetailResponse(SessionResponse):
    model_config = ConfigDict(from_attributes=True)

    preset_values: list[PresetValueResponse]
    alerts: list[AlertResponse]
    field_observations: list["FieldObservationResponse"] = []
    total_duration_minutes: Optional[float]


class SessionSummaryReport(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    operation_type: str
    status: str
    tractor_id: str
    implement_id: Optional[str]
    operator_id: str
    operator_name: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime]
    duration_minutes: Optional[float]
    area_ha: Optional[float]
    total_distance_m: Optional[float] = None
    total_cost_inr: Optional[float]
    charge_per_ha_applied: Optional[float]
    cost_note: Optional[str]
    alerts: list[AlertSummaryItem]
    field_observations: list["FieldObservationResponse"] = []
    observations_count: int = 0
    metrics: list[SessionMetricSummary] = []
    preset_summaries: list[PresetSummaryItem] = []
    total_alerts: int
    unacknowledged_alerts: int


class FieldObservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    obs_type: str
    value: float
    unit: str
    lat: Optional[float]
    lon: Optional[float]
    notes: Optional[str]
    recorded_at: datetime


class WageRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    operator_id: uuid.UUID
    rate_type: str
    rate_amount: float
    area_ha: Optional[float]
    duration_hours: Optional[float]
    total_amount: Optional[float]
    approved: bool
    approved_at: Optional[datetime]
    disputed: bool
    dispute_reason: Optional[str]
    created_at: datetime


class FuelLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tractor_id: uuid.UUID
    session_id: Optional[uuid.UUID]
    litres: float
    refilled_at: datetime
    cost_per_litre: Optional[float]
    total_cost: Optional[float]
    notes: Optional[str]
    created_at: datetime


SessionDetailResponse.model_rebuild()
SessionSummaryReport.model_rebuild()

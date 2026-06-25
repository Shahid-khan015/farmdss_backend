# Follows pattern from: app/schemas/tractor.py (Pydantic v2 models)
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

StatusLevel = Literal["normal", "warning", "critical"]


class LatestFeedReading(BaseModel):
    """One row per configured feed for GET /iot/latest (matches mobile contract)."""

    model_config = ConfigDict(from_attributes=True)

    feed_key: str
    raw_value: Optional[str] = None
    numeric_value: Optional[float] = None
    unit: str
    device_timestamp: Optional[datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    status_label: StatusLevel


class IotLatestResponse(BaseModel):
    device_id: str
    feeds: list[LatestFeedReading]

    # Tractor operation interpretation — computed from GPS, PTO, vibration feeds
    interpretation: Optional[str] = None       # Human-readable state label
    state_key: Optional[str] = None            # Machine-readable key (e.g. "pto_field_work")
    state_color: Optional[str] = None          # Badge hex colour for UI
    gps_changed: Optional[bool] = None         # Whether GPS moved between last two readings
    pto_rotating: Optional[bool] = None        # Whether PTO shaft speed > threshold
    vibrating: Optional[bool] = None           # Whether vibration > threshold
    signals_available: bool = False            # False when no signal data was available


class IotHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    feed_key: str
    device_id: str
    raw_value: str
    numeric_value: Optional[float]
    unit: str
    latitude: Optional[float]
    longitude: Optional[float]
    device_timestamp: datetime
    session_id: Optional[str]
    created_at: datetime


class IotHistoryResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[IotHistoryItem]

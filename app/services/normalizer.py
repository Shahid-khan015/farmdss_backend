# Follows pattern from: app/models/tractor.py (dataclasses N/A; pure parsing utilities)
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Mapping, Optional

logger = logging.getLogger(__name__)

# Canonical feed keys ↔ Adafruit dashboard feed paths (username segment is env-specific).
FEEDS: Final[dict[str, str]] = {
    "soil_moisture": "abhixs/feeds/soil-moisture",
    "position_tracking": "abhixs/feeds/gpsloc",
    "forward_speed": "abhixs/feeds/forward-speed",
    "pto_shaft_speed": "abhixs/feeds/pto-speed",
    "depth_of_operation": "abhixs/feeds/operation-depth",
    "machine_status": "abhixs/feeds/machine-status",
    "gearbox_temperature": "abhixs/feeds/gearbox-temp",
    "vibration": "abhixs/feeds/vibration-level",
    "wheel_slip": "abhixs/feeds/wheel-slip",
    "field_capacity": "abhixs/feeds/field-capacity",
}

# Explicit units for dashboard / analytics (not inferred from Adafruit metadata here).
FEED_UNITS: Final[dict[str, str]] = {
    "soil_moisture": "%",
    "position_tracking": "",
    "forward_speed": "km/h",
    "pto_shaft_speed": "rpm",
    "depth_of_operation": "cm",
    "machine_status": "",
    "gearbox_temperature": "°C",
    "vibration": "g",
    "wheel_slip": "%",
    "field_capacity": "ha/h",
}


def adafruit_slug_for_feed_key(feed_key: str) -> str:
    path = FEEDS[feed_key]
    return path.split("/feeds/")[-1]


def feed_key_from_adafruit_topic_or_slug(topic_or_slug: str) -> Optional[str]:
    """Map `username/feeds/slug` or bare `slug` to our canonical feed_key."""
    slug = topic_or_slug.strip("/").split("/")[-1]
    slug_norm = slug.lower().replace("_", "-")
    for key, path in FEEDS.items():
        if path.split("/feeds/")[-1].lower().replace("_", "-") == slug_norm:
            return key
    return None


@dataclass(frozen=True)
class NormalizedReading:
    device_id: str
    feed_key: str
    raw_value: str
    numeric_value: float | None
    unit: str
    latitude: float | None
    longitude: float | None
    device_timestamp: datetime
    adafruit_id: str
    session_id: uuid.UUID | None = None


def _parse_device_timestamp(raw: Mapping[str, Any]) -> datetime:
    for k in ("created_at", "created", "timestamp", "updated_at"):
        val = raw.get(k)
        if isinstance(val, str) and val.strip():
            s = val.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _safe_json_dict(value_str: str) -> dict[str, Any] | None:
    try:
        parsed: Any = json.loads(value_str)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_lat_lon_from_obj(obj: Mapping[str, Any]) -> tuple[float | None, float | None]:
    lat_keys = ("lat", "latitude", "Lat", "LAT")
    lon_keys = ("lon", "lng", "longitude", "Lon", "LON")
    lat = next((obj.get(k) for k in lat_keys if k in obj), None)
    lon = next((obj.get(k) for k in lon_keys if k in obj), None)
    out_lat: float | None = None
    out_lon: float | None = None
    try:
        if lat is not None:
            out_lat = float(lat)
        if lon is not None:
            out_lon = float(lon)
    except (TypeError, ValueError):
        return None, None
    return out_lat, out_lon


def _parse_numeric(value_str: str) -> float | None:
    s = value_str.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", s)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
        return None


def process_iot_data(
    feed_key: str,
    raw_record: Mapping[str, Any],
    *,
    default_device_id: str = "default",
) -> NormalizedReading | None:
    """
    Stateless normalization: parse Adafruit REST row or a synthetic MQTT-shaped dict.
    Returns None if the record cannot be turned into a durable identity (malformed id).
    """
    if feed_key not in FEEDS:
        logger.warning("Unknown feed_key %r; skipping", feed_key)
        return None

    value_field = raw_record.get("value")
    if value_field is None:
        logger.debug("Missing value for feed %s", feed_key)
        return None
    raw_value = value_field if isinstance(value_field, str) else json.dumps(value_field)

    adafruit_id = raw_record.get("id")
    if adafruit_id is not None:
        adafruit_id = str(adafruit_id)
    if not adafruit_id:
        # MQTT and other transports without Adafruit point id: synthetic key (no cross-transport dedup).
        adafruit_id = f"mqtt-{uuid.uuid4()}"

    device_id = raw_record.get("device_id") or default_device_id
    if not isinstance(device_id, str):
        device_id = str(device_id)

    sid = raw_record.get("session_id")
    session_uuid: uuid.UUID | None = None
    if sid:
        try:
            session_uuid = uuid.UUID(str(sid))
        except ValueError:
            session_uuid = None

    unit = FEED_UNITS.get(feed_key, "")
    lat: float | None = raw_record.get("lat") if isinstance(raw_record.get("lat"), (int, float)) else None
    lon: float | None = raw_record.get("lon") if isinstance(raw_record.get("lon"), (int, float)) else None
    if lat is None and isinstance(raw_record.get("latitude"), (int, float)):
        lat = float(raw_record["latitude"])
    if lon is None and isinstance(raw_record.get("longitude"), (int, float)):
        lon = float(raw_record["longitude"])

    numeric_value: float | None = None
    if feed_key == "position_tracking":
        parsed = _safe_json_dict(raw_value)
        if parsed:
            glat, glon = _extract_lat_lon_from_obj(parsed)
            if glat is not None:
                lat = glat
            if glon is not None:
                lon = glon
        else:
            # Comma-separated fallback: "lat,lon"
            parts = [p.strip() for p in raw_value.split(",")]
            if len(parts) >= 2:
                try:
                    lat = float(parts[0])
                    lon = float(parts[1])
                except ValueError:
                    pass
    else:
        numeric_value = _parse_numeric(raw_value)

    return NormalizedReading(
        device_id=device_id,
        feed_key=feed_key,
        raw_value=raw_value,
        numeric_value=numeric_value,
        unit=unit,
        latitude=lat,
        longitude=lon,
        device_timestamp=_parse_device_timestamp(raw_record),
        adafruit_id=adafruit_id,
        session_id=session_uuid,
    )

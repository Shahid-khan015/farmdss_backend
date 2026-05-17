from __future__ import annotations

import json
import logging
import math
import uuid
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import or_

GPS_FEED_KEYS = ("position_tracking", "gpsloc")
MAX_REASONABLE_GPS_STEP_M = 500.0

logger = logging.getLogger(__name__)


def parse_gps_points(session_id: uuid.UUID, db: Session) -> List[Tuple[float, float]]:
    from app.models.iot_reading import IoTReading

    points: List[Tuple[float, float]] = []
    try:
        rows = (
            db.query(IoTReading)
            .filter(
                IoTReading.session_id == session_id,
                or_(
                    IoTReading.feed_key == GPS_FEED_KEYS[0],
                    IoTReading.feed_key == GPS_FEED_KEYS[1],
                ),
            )
            .order_by(IoTReading.device_timestamp.asc())
            .all()
        )
    except Exception:
        return []

    for reading in rows:
        lat: Optional[float] = None
        lon: Optional[float] = None

        try:
            parsed = json.loads(reading.raw_value)
            if isinstance(parsed, dict):
                lat_raw = parsed.get("lat")
                lon_raw = parsed.get("lon")
                if lat_raw is not None and lon_raw is not None:
                    lat = float(lat_raw)
                    lon = float(lon_raw)
        except Exception:
            pass

        if lat is None or lon is None:
            lat_raw = getattr(reading, "lat", None)
            lon_raw = getattr(reading, "lon", None)
            if lat_raw is None:
                lat_raw = getattr(reading, "latitude", None)
            if lon_raw is None:
                lon_raw = getattr(reading, "longitude", None)
            if lat_raw is not None and lon_raw is not None:
                try:
                    lat = float(lat_raw)
                    lon = float(lon_raw)
                except (TypeError, ValueError):
                    lat = None
                    lon = None

        if lat is None or lon is None:
            continue
        if math.isnan(lat) or math.isnan(lon):
            continue

        points.append((lat, lon))

    return points


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1 = lat1 * math.pi / 180.0
    phi2 = lat2 * math.pi / 180.0
    dphi = (lat2 - lat1) * math.pi / 180.0
    dlambda = (lon2 - lon1) * math.pi / 180.0
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def compute_polygon_area_ha(points: List[Tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0

    r_deg = 111320.0
    xy = []
    for lat, lon in points:
        x_i = lon * r_deg * math.cos(lat * math.pi / 180.0)
        y_i = lat * r_deg
        xy.append((x_i, y_i))

    area_twice = 0.0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        area_twice += (x1 * y2) - (x2 * y1)

    area_m2 = abs(area_twice) / 2.0
    return area_m2 / 10000.0


def compute_covered_area_ha(
    points: List[Tuple[float, float]],
    implement_width_m: Optional[float],
) -> float:
    if implement_width_m is None or implement_width_m <= 0:
        return compute_polygon_area_ha(points)

    if len(points) < 2:
        return 0.0

    total_path_m = compute_total_path_distance_m(points)

    # For field operations the worked area is the covered swath: path length x implement width.
    # Polygon area is only a fallback when implement width is unknown; back-and-forth passes often
    # produce a near-zero or self-crossing polygon and should not cap the worked area.
    covered_area_m2 = total_path_m * implement_width_m
    return covered_area_m2 / 10000.0


def _filtered_path_segments(points: List[Tuple[float, float]]) -> List[float]:
    segments: List[float] = []
    for i in range(len(points) - 1):
        lat1, lon1 = points[i]
        lat2, lon2 = points[i + 1]
        distance = haversine_distance_m(lat1, lon1, lat2, lon2)
        if distance <= 0.25:
            continue
        if distance > MAX_REASONABLE_GPS_STEP_M:
            logger.info(
                "field_area.gps_step_ignored",
                extra={"distance_m": distance, "lat1": lat1, "lon1": lon1, "lat2": lat2, "lon2": lon2},
            )
            continue
        segments.append(distance)
    return segments


def compute_total_path_distance_m(points: List[Tuple[float, float]]) -> float:
    """Return the total length of the GPS path in metres."""
    if len(points) < 2:
        return 0.0
    return sum(_filtered_path_segments(points))


def finalize_session_area(session_id: uuid.UUID, db: Session) -> float:
    from app.models.session import OperationSession

    points = parse_gps_points(session_id, db)
    session = db.query(OperationSession).filter(OperationSession.id == session_id).first()
    if session is None:
        return 0.0

    area = compute_covered_area_ha(points, session.implement_width_m)
    session.area_ha = round(max(0.0, area), 4)
    logger.info(
        "field_area.finalized",
        extra={
            "session_id": str(session_id),
            "gps_points": len(points),
            "implement_width_m": session.implement_width_m,
            "area_ha": session.area_ha,
            "distance_m": compute_total_path_distance_m(points),
        },
    )
    db.flush()
    return area

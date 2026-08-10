# Follows pattern from: app/crud/tractor.py (select + scalars)
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.models.iot_reading import IoTReading
from app.services.normalizer import FEEDS


def get_latest_per_feed(
    db: Session,
    *,
    device_id: str,
) -> dict[str, IoTReading | None]:
    """Latest row per canonical feed_key for one device (bounded fan-out: len(FEEDS))."""
    out: dict[str, IoTReading | None] = {k: None for k in FEEDS}
    for fk in FEEDS:
        stmt = (
            select(IoTReading)
            .where(and_(IoTReading.device_id == device_id, IoTReading.feed_key == fk))
            .order_by(desc(IoTReading.device_timestamp))
            .limit(1)
        )
        out[fk] = db.scalars(stmt).first()
    return out


def get_history(
    db: Session,
    *,
    feed_key: str,
    device_id: Optional[str] = None,
    session_id: Optional[uuid.UUID] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[int, list[IoTReading]]:
    filters = [IoTReading.feed_key == feed_key]
    if device_id is not None:
        filters.append(IoTReading.device_id == device_id)
    if session_id is not None:
        filters.append(IoTReading.session_id == session_id)
    if start is not None:
        filters.append(IoTReading.device_timestamp >= start)
    if end is not None:
        filters.append(IoTReading.device_timestamp <= end)

    cond = and_(*filters) if len(filters) > 1 else filters[0]
    total = int(db.scalar(select(func.count()).select_from(IoTReading).where(cond)) or 0)

    stmt = (
        select(IoTReading)
        .where(cond)
        .order_by(desc(IoTReading.device_timestamp))
        .offset(offset)
        .limit(min(limit, 5000))
    )
    rows = list(db.scalars(stmt).all())
    return total, rows

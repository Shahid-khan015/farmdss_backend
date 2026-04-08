# Follows pattern from: app/api/v1/routes/tractors.py (APIRouter, Depends(get_db), Session)
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.iot_reading import IoTReading
from app.schemas.iot import IotHistoryItem, IotHistoryResponse, IotLatestResponse, LatestFeedReading
from app.services.alert_engine import get_status_label
from app.services.iot_query import get_history, get_latest_per_feed
from app.services.normalizer import FEED_UNITS, FEEDS

router = APIRouter()


def _to_latest_item(feed_key: str, row: IoTReading | None) -> LatestFeedReading:
    if row is None:
        return LatestFeedReading(
            feed_key=feed_key,
            raw_value=None,
            numeric_value=None,
            unit=FEED_UNITS.get(feed_key, ""),
            device_timestamp=None,
            lat=None,
            lon=None,
            status_label="normal",
        )
    status = get_status_label(row.feed_key, row.numeric_value)
    return LatestFeedReading(
        feed_key=feed_key,
        raw_value=row.raw_value,
        numeric_value=row.numeric_value,
        unit=row.unit or FEED_UNITS.get(feed_key, ""),
        device_timestamp=row.device_timestamp,
        lat=row.latitude,
        lon=row.longitude,
        status_label=status,
    )


@router.get("/latest", response_model=IotLatestResponse)
def iot_latest(
    device_id: str = Query(default="default"),
    db: Session = Depends(get_db),
):
    latest = get_latest_per_feed(db, device_id=device_id)
    feeds = [_to_latest_item(fk, latest[fk]) for fk in FEEDS]
    return IotLatestResponse(device_id=device_id, feeds=feeds)


@router.get("/history", response_model=IotHistoryResponse)
def iot_history(
    feed_key: str = Query(..., description="Canonical feed key, e.g. soil_moisture"),
    device_id: Optional[str] = Query(default=None),
    session_id: Optional[uuid.UUID] = Query(default=None),
    start: Optional[datetime] = Query(default=None),
    end: Optional[datetime] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if feed_key not in FEEDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown feed_key")
    total, rows = get_history(
        db,
        feed_key=feed_key,
        device_id=device_id,
        session_id=session_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    items = [
        IotHistoryItem(
            id=str(r.id),
            feed_key=r.feed_key,
            device_id=r.device_id,
            raw_value=r.raw_value,
            numeric_value=r.numeric_value,
            unit=r.unit,
            latitude=r.latitude,
            longitude=r.longitude,
            device_timestamp=r.device_timestamp,
            session_id=str(r.session_id) if r.session_id else None,
            created_at=r.created_at,
        )
        for r in rows
    ]
    return IotHistoryResponse(total=total, limit=limit, offset=offset, items=items)

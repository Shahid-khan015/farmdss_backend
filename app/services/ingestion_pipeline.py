# Follows pattern from: app/database.py (SessionLocal), app/api/deps.py (Session lifecycle)
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.iot_reading import IoTReading
from app.models.session import OperationSession
from app.services.alert_engine import evaluate
from app.services.normalizer import NormalizedReading

logger = logging.getLogger(__name__)


def broadcast_update(reading: IoTReading) -> None:
    """Future: WebSocket fan-out to dashboards; keep as no-op until socket layer exists."""
    _ = reading


def ingest_reading(
    db: Session,
    normalized: NormalizedReading,
    *,
    commit: bool = True,
) -> tuple[bool, IoTReading | None]:
    """
    Dedup by adafruit_id, persist, then alert hook + broadcast hook.
    Returns (inserted, row_if_present).
    """
    new_id = uuid.uuid4()
    active_session = db.query(OperationSession).filter(
        OperationSession.status.in_(("active", "paused"))
    ).order_by(OperationSession.started_at.desc()).first()
    values = {
        "id": new_id,
        "device_id": normalized.device_id,
        "feed_key": normalized.feed_key,
        "raw_value": normalized.raw_value,
        "numeric_value": normalized.numeric_value,
        "unit": normalized.unit,
        "latitude": normalized.latitude,
        "longitude": normalized.longitude,
        "device_timestamp": normalized.device_timestamp,
        "adafruit_id": normalized.adafruit_id,
        "session_id": active_session.id if active_session is not None else None,
    }

    try:
        # ORM insert only. Avoid dialect-specific INSERT..ON CONFLICT..RETURNING: result/first()
        # behavior differs across drivers and often reports no row even when a row was stored.
        exists = db.scalars(
            select(IoTReading).where(IoTReading.adafruit_id == normalized.adafruit_id)
        ).first()
        if exists is not None:
            inserted = False
        else:
            db.add(IoTReading(**values))
            inserted = True
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError:
        db.rollback()
        return False, None
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("ingest_reading failed adafruit_id=%s: %s", normalized.adafruit_id, exc)
        raise

    if not inserted:
        return False, None

    loaded = db.scalars(
        select(IoTReading).where(IoTReading.adafruit_id == normalized.adafruit_id)
    ).first()

    if loaded is None:
        return True, None

    evaluate(loaded, db)
    broadcast_update(loaded)
    if commit:
        db.commit()
    return True, loaded


def ingest_normalized_batch(db: Session, items: list[NormalizedReading]) -> int:
    """Insert many in one transaction; returns count of new rows."""
    n = 0
    try:
        for item in items:
            inserted, _ = ingest_reading(db, item, commit=False)
            if inserted:
                n += 1
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise
    return n

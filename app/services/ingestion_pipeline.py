# Follows pattern from: app/database.py (SessionLocal), app/api/deps.py (Session lifecycle)
from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.iot_reading import IoTReading
from app.models.session import OperationSession
from app.services.alert_engine import evaluate
from app.services.normalizer import NormalizedReading

logger = logging.getLogger(__name__)

# Telemetry arriving while an operation is paused still belongs to that operation - dropping it
# punches holes in the GPS path and under-reports the worked area at finalize time.
ATTACHABLE_SESSION_STATUSES = ("active", "paused")


def broadcast_update(reading: IoTReading) -> None:
    """Future: WebSocket fan-out to dashboards; keep as no-op until socket layer exists."""
    _ = reading


def resolve_target_session(
    db: Session, preferred_session_id: Optional[uuid.UUID] = None
) -> Optional[OperationSession]:
    """
    Pick the session new readings belong to. Resolved once per batch: this is a network round
    trip, and every row in a cycle lands on the same session anyway.
    """
    if preferred_session_id is not None:
        preferred = db.scalars(
            select(OperationSession).where(
                OperationSession.id == preferred_session_id,
                OperationSession.status.in_(ATTACHABLE_SESSION_STATUSES),
                OperationSession.gps_tracking_enabled.is_(True),
            )
        ).first()
        if preferred is not None:
            return preferred

    candidates = list(
        db.scalars(
            select(OperationSession)
            .where(
                OperationSession.status.in_(ATTACHABLE_SESSION_STATUSES),
                OperationSession.gps_tracking_enabled.is_(True),
            )
            .order_by(OperationSession.started_at.desc())
            .limit(2)
        )
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        # Adafruit REST rows carry no device->session binding, so concurrent operators are
        # indistinguishable here and everything lands on the most recent session.
        logger.warning(
            "Multiple attachable GPS sessions; attributing readings to the most recent (%s). "
            "Per-device session binding is needed for concurrent operators.",
            candidates[0].id,
        )
    return candidates[0]


def _values_for(normalized: NormalizedReading, session_id: Optional[uuid.UUID]) -> dict:
    return {
        "id": uuid.uuid4(),
        "device_id": normalized.device_id,
        "feed_key": normalized.feed_key,
        "raw_value": normalized.raw_value,
        "numeric_value": normalized.numeric_value,
        "unit": normalized.unit,
        "latitude": normalized.latitude,
        "longitude": normalized.longitude,
        "device_timestamp": normalized.device_timestamp,
        "adafruit_id": normalized.adafruit_id,
        "session_id": session_id,
    }


def _existing_adafruit_ids(db: Session, ids: List[str]) -> Set[str]:
    """One SELECT for the whole batch instead of one per row."""
    if not ids:
        return set()
    found: Set[str] = set()
    # Chunked to stay well clear of driver bind-parameter limits.
    chunk = 500
    for start in range(0, len(ids), chunk):
        window = ids[start : start + chunk]
        rows = db.scalars(
            select(IoTReading.adafruit_id).where(IoTReading.adafruit_id.in_(window))
        ).all()
        found.update(rows)
    return found


def ingest_normalized_batch(db: Session, items: List[NormalizedReading]) -> int:
    """
    Persist a batch: resolve the session once, dedup in one query, insert in one flush.

    Replaces a per-row implementation that cost ~3 round trips per reading - fine against a
    Postgres on localhost, several seconds per cycle against a managed one across the network.
    Returns the count of newly stored rows.
    """
    inserted, _ = ingest_normalized_batch_rows(db, items)
    return inserted


def ingest_normalized_batch_rows(
    db: Session, items: List[NormalizedReading]
) -> Tuple[int, List[IoTReading]]:
    """Same as ``ingest_normalized_batch`` but also returns the stored ORM rows."""
    if not items:
        return 0, []

    target_session = resolve_target_session(
        db, next((i.session_id for i in items if i.session_id is not None), None)
    )
    session_id = target_session.id if target_session is not None else None
    # A paused operation keeps collecting telemetry, but a stationary machine should not raise
    # deviation alerts against its presets.
    alerts_enabled = target_session is not None and target_session.status == "active"

    # Collapse duplicates inside the batch itself; the unique index would reject them anyway.
    unique: Dict[str, NormalizedReading] = {}
    for item in items:
        unique.setdefault(item.adafruit_id, item)

    try:
        existing = _existing_adafruit_ids(db, list(unique.keys()))
        fresh = [n for key, n in unique.items() if key not in existing]
        if not fresh:
            return 0, []

        rows = [IoTReading(**_values_for(n, session_id)) for n in fresh]
        db.add_all(rows)
        db.flush()
    except IntegrityError:
        # Another writer inserted an overlapping id between the SELECT and the flush.
        db.rollback()
        return _ingest_rows_individually(db, list(unique.values()), session_id, alerts_enabled)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("ingest_normalized_batch failed for %s reading(s): %s", len(unique), exc)
        raise

    if alerts_enabled:
        for row in rows:
            evaluate(row, db)
    for row in rows:
        broadcast_update(row)

    db.commit()
    return len(rows), rows


def _ingest_rows_individually(
    db: Session,
    items: List[NormalizedReading],
    session_id: Optional[uuid.UUID],
    alerts_enabled: bool,
) -> Tuple[int, List[IoTReading]]:
    """Fallback for a mid-batch unique-constraint race: isolate each row in a savepoint."""
    stored: List[IoTReading] = []
    for normalized in items:
        try:
            with db.begin_nested():
                row = IoTReading(**_values_for(normalized, session_id))
                db.add(row)
        except IntegrityError:
            continue
        except SQLAlchemyError as exc:
            logger.exception(
                "ingest row failed adafruit_id=%s: %s", normalized.adafruit_id, exc
            )
            continue
        stored.append(row)

    if alerts_enabled:
        for row in stored:
            evaluate(row, db)
    for row in stored:
        broadcast_update(row)

    db.commit()
    return len(stored), stored


def ingest_reading(
    db: Session,
    normalized: NormalizedReading,
    *,
    commit: bool = True,
) -> Tuple[bool, Optional[IoTReading]]:
    """
    Single-row entry point (MQTT transport, simulator scripts), implemented over the batch path.
    Returns (inserted, row_if_stored).

    ``commit`` is accepted for call-site compatibility but is now always effectively True: the
    batch path owns its transaction so a single row cannot be left half-written.
    """
    _ = commit
    count, rows = ingest_normalized_batch_rows(db, [normalized])
    if count == 0:
        return False, None
    return True, rows[0]

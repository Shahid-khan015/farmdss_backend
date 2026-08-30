"""
Fetch-through: pull from Adafruit inline when the stored telemetry is stale.

The background poller alone is not enough on a host that suspends idle services. After a
spin-down the poller thread has only just restarted, so the first dashboard request would be
answered from whatever rows predate the sleep. Fetching on read means that first request both
wakes the service and returns live values.

Every path here fails open: a slow or broken Adafruit call logs and leaves the caller serving
whatever the database already holds. Telemetry freshness is never worth a 5xx.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.iot_reading import IoTReading

logger = logging.getLogger(__name__)

# Serializes fetch-through across concurrent requests so a burst of dashboard polls produces one
# upstream fetch, not one per request.
_fetch_lock = threading.Lock()
_last_attempt_monotonic: float = 0.0
_LOCK_WAIT_SEC = 3.0


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes for timezone-aware columns; Postgres does not."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def latest_timestamp(db: Session, device_id: str) -> Optional[datetime]:
    newest = db.scalar(
        select(func.max(IoTReading.device_timestamp)).where(IoTReading.device_id == device_id)
    )
    return _as_utc(newest)


def seconds_since_latest(db: Session, device_id: str) -> Optional[float]:
    newest = latest_timestamp(db, device_id)
    if newest is None:
        return None
    return (datetime.now(timezone.utc) - newest).total_seconds()


def is_stale(db: Session, device_id: str) -> bool:
    age = seconds_since_latest(db, device_id)
    if age is None:
        return True
    return age > float(settings.IOT_STALE_AFTER_SEC)


def refresh_now(*, limit: Optional[int] = None, reason: str = "manual") -> int:
    """
    Unconditional fetch + store. Returns rows stored, or 0 on any failure.

    Imported lazily so this module stays importable when the transports are not in use.
    """
    global _last_attempt_monotonic
    from app.services.transports import http_poller

    started = time.monotonic()
    try:
        batch, failed = http_poller.fetch_all_feeds(
            limit=limit, budget_sec=float(settings.IOT_FETCH_THROUGH_TIMEOUT_SEC)
        )
        stored = http_poller.store_readings(batch)
        _last_attempt_monotonic = time.monotonic()
        http_poller.STATUS.last_fetch_through_at = datetime.now(timezone.utc).isoformat()
        http_poller.STATUS.fetch_through_total += 1
        logger.info(
            "IoT fetch-through (%s): fetched=%s stored=%s ms=%s%s",
            reason,
            len(batch),
            stored,
            int((time.monotonic() - started) * 1000),
            (" failed=" + ",".join(failed)) if failed else "",
        )
        return stored
    except Exception:
        _last_attempt_monotonic = time.monotonic()
        logger.exception("IoT fetch-through failed (%s); serving stored data", reason)
        return 0


def ensure_fresh(db: Session, device_id: str, *, reason: str = "read") -> bool:
    """
    Refresh from Adafruit if this device's newest reading is older than IOT_STALE_AFTER_SEC.

    Returns True when a fetch actually ran. Concurrent callers coalesce: the first one fetches,
    the rest wait briefly and then re-check rather than issuing their own request.
    """
    global _last_attempt_monotonic

    if not settings.IOT_FETCH_THROUGH_ENABLED:
        return False
    if not settings.AIO_USERNAME or not settings.AIO_KEY:
        return False
    if not is_stale(db, device_id):
        return False

    cooldown = float(settings.IOT_FETCH_THROUGH_COOLDOWN_SEC)
    if _last_attempt_monotonic and (time.monotonic() - _last_attempt_monotonic) < cooldown:
        return False

    if not _fetch_lock.acquire(timeout=_LOCK_WAIT_SEC):
        logger.debug("fetch-through skipped: another refresh is in flight")
        return False
    try:
        # Another caller may have refreshed while we waited for the lock.
        if _last_attempt_monotonic and (time.monotonic() - _last_attempt_monotonic) < cooldown:
            return False
        stored = refresh_now(reason=reason)
    finally:
        _fetch_lock.release()

    if stored:
        # The rows were written by a different Session. End this read-only transaction so the
        # caller's next query starts a new snapshot and observes them. Callers of ensure_fresh
        # must therefore have no pending writes - the session-start path uses refresh_now instead.
        db.rollback()
    return True

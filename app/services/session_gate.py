"""The dormancy switch: is any operation session running right now?

Transports park on :meth:`SessionGate.wait_for_change` while the gate is closed, so an
idle deployment makes no Adafruit calls, no database writes, and holds no database
connections at all. Lifecycle routes call :meth:`SessionGate.refresh` after committing
a status change.
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import select

from app.database import SessionLocal
from app.models.session import OperationSession
from app.services.ingestion_pipeline import ATTACHABLE_SESSION_STATUSES

logger = logging.getLogger(__name__)


def release_pool() -> None:
    """Drop every idle pooled connection so a serverless Postgres can actually suspend.

    SQLAlchemy's ``QueuePool`` keeps connections open indefinitely once established, and
    ``pool_recycle`` only acts at checkout time -- so an idle pool holds the Neon compute
    awake and bills compute-hours for a deployment doing nothing at all. Disposing on the
    transition to CLOSED is the only reliable way to let it sleep.

    Safe while requests are in flight: ``dispose()`` swaps in a fresh pool, and a
    connection currently checked out is detached and closed when its owner returns it.
    Callers must therefore only ever dispose *after* committing, never mid-transaction --
    ``stop_session`` satisfies this by calling ``GATE.refresh()`` as its last statement.

    The next checkout simply opens a new connection, which is exactly the reconnect a
    waking Neon compute needs anyway.
    """
    from app.database import engine, is_sqlite_fallback_active

    if is_sqlite_fallback_active():
        # Nothing to release: the fallback is a local file, and disposing would only
        # churn handles for no benefit.
        return
    try:
        engine.dispose()
        logger.info("session gate CLOSED -> connection pool disposed; database may suspend")
    except Exception:
        logger.exception("pool dispose on gate close failed; connections may stay open")


class SessionGate:
    """Open while any session is attachable (``active`` or ``paused``); closed otherwise.

    ``refresh()`` re-reads the authoritative answer from the database rather than
    maintaining a counter: a counter drifts across restarts and concurrent operators,
    one indexed query does not.

    **Paused sessions keep the gate OPEN.** Telemetry must keep attaching so the GPS
    trail stays continuous for ``finalize_session_area``; only alert evaluation is
    suppressed while paused, and that decision lives in ``ingestion_pipeline``.
    """

    def __init__(self) -> None:
        self._open = False
        self._changed = threading.Event()
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def refresh(self) -> bool:
        """Re-read from the database and wake any waiting transport. Never raises."""
        db = None
        try:
            # Inside the try on purpose: with a suspended or unreachable database,
            # SessionLocal() itself is what raises -- which is precisely the case the
            # fail-open guarantee below exists to cover.
            db = SessionLocal()
            found = db.scalars(
                select(OperationSession.id)
                .where(
                    OperationSession.status.in_(ATTACHABLE_SESSION_STATUSES),
                    OperationSession.gps_tracking_enabled.is_(True),
                )
                .limit(1)
            ).first()
            is_open = found is not None
        except Exception:
            # Fail open: a transient database error must not silently stop ingestion
            # during a live operation.
            logger.exception("session gate refresh failed; leaving gate open")
            is_open = True
        finally:
            # Return this query's connection to the pool BEFORE any dispose below, so
            # it is one of the connections actually released.
            if db is not None:
                db.close()

        with self._lock:
            changed = is_open != self._open
            self._open = is_open

        if changed:
            logger.info("session gate -> %s", "OPEN" if is_open else "CLOSED")
            if not is_open:
                release_pool()
            self._changed.set()
        return is_open

    def wait_for_change(self, timeout: float) -> bool:
        """Block until the gate flips or ``timeout`` elapses. True if it flipped.

        The timeout is a safety net, not the mechanism: it lets a transport re-check
        the database periodically in case a status changed without ``refresh()`` being
        called (a restart mid-session, say).
        """
        flipped = self._changed.wait(timeout=timeout)
        if flipped:
            self._changed.clear()
        return flipped

    def wake(self) -> None:
        """Unblock anything parked in ``wait_for_change`` -- used on shutdown."""
        self._changed.set()

    def snapshot(self) -> dict:
        return {"open": self.is_open()}


#: Process-wide singleton. One uvicorn worker only -- see render.yaml.
GATE = SessionGate()

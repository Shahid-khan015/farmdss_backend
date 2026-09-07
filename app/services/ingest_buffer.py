"""Micro-batch buffer for inbound telemetry, with a real flush barrier.

MQTT delivers one message per reading. Writing each one straight through would mean a
transaction per message against a managed Postgres across the network; coalescing them
into one write every few seconds cuts that by an order of magnitude without adding
latency the operator can perceive, because the WebSocket hot path is fed *before* the
buffer, not from it.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Iterable, List, Optional

from app.database import SessionLocal
from app.services.ingestion_pipeline import ingest_normalized_batch_rows
from app.services.normalizer import NormalizedReading

logger = logging.getLogger(__name__)


class ReadingBuffer:
    """Coalesce readings into one write per few seconds instead of one per message.

    **Two locks, on purpose.** ``_items_lock`` is held only for the microseconds it
    takes to append to or swap the list, so an ingestion thread is never blocked
    behind a database round trip. ``_write_lock`` serialises the writes themselves, so
    a scheduled flush and a stop-triggered flush cannot interleave into two concurrent
    transactions over the same rows.

    **``flush()`` is the barrier.** When it returns, everything buffered at the moment
    it was called is committed. ``stop_session`` calls it before finalizing the field
    area, because area -- and therefore the bill -- is computed from *stored* GPS rows:
    a point still sitting in memory is a point the farmer is not charged for.
    """

    def __init__(self, max_items: int = 20, max_age_sec: float = 4.0) -> None:
        self.max_items = max(1, int(max_items))
        self.max_age_sec = max(0.5, float(max_age_sec))
        self._items: List[NormalizedReading] = []
        self._oldest_at: Optional[float] = None
        self._items_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.flushes = 0
        self.stored_total = 0
        self.dropped_total = 0
        self.last_flush_ms: Optional[int] = None

    # --- producer side (ingestion threads) -----------------------------------

    def add(self, reading: NormalizedReading) -> None:
        with self._items_lock:
            if not self._items:
                self._oldest_at = time.monotonic()
            self._items.append(reading)

    def add_many(self, readings: Iterable[NormalizedReading]) -> None:
        batch = list(readings)
        if not batch:
            return
        with self._items_lock:
            if not self._items:
                self._oldest_at = time.monotonic()
            self._items.extend(batch)

    def pending(self) -> int:
        with self._items_lock:
            return len(self._items)

    def is_due(self) -> bool:
        with self._items_lock:
            if not self._items:
                return False
            if len(self._items) >= self.max_items:
                return True
            return (
                self._oldest_at is not None
                and (time.monotonic() - self._oldest_at) >= self.max_age_sec
            )

    def _take(self) -> List[NormalizedReading]:
        with self._items_lock:
            items = self._items
            self._items = []
            self._oldest_at = None
            return items

    # --- consumer side -------------------------------------------------------

    def flush(self) -> int:
        """Drain and persist everything buffered. Returns rows stored.

        Safe from any thread. Returns only once the write has committed, which is what
        makes it usable as a barrier before ``finalize_session_area``.
        """
        with self._write_lock:
            items = self._take()
            if not items:
                return 0

            started = time.monotonic()
            db = None
            try:
                # Inside the try: when the database is unreachable, SessionLocal() is
                # what raises. `stop_session` calls flush(), so an escape here would
                # turn a transient outage into a failed session stop.
                db = SessionLocal()
                stored, _rows = ingest_normalized_batch_rows(db, items)
            except Exception:
                # The readings are already out of the list; re-raising would strand
                # them silently. Count them and carry on -- the next poll cycle
                # backfills from Adafruit, which is the authoritative store.
                self.dropped_total += len(items)
                logger.exception(
                    "buffer flush failed; %s reading(s) not persisted (backfill will recover)",
                    len(items),
                )
                return 0
            finally:
                if db is not None:
                    db.close()

            self.flushes += 1
            self.stored_total += stored
            self.last_flush_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "buffer flush: buffered=%s stored=%s ms=%s",
                len(items),
                stored,
                self.last_flush_ms,
            )
            return stored

    def flush_if_due(self) -> int:
        return self.flush() if self.is_due() else 0

    def snapshot(self) -> dict:
        return {
            "pending": self.pending(),
            "flushes": self.flushes,
            "stored_total": self.stored_total,
            "dropped_total": self.dropped_total,
            "last_flush_ms": self.last_flush_ms,
            "max_items": self.max_items,
            "max_age_sec": self.max_age_sec,
        }


def _build_buffer() -> ReadingBuffer:
    from app.config import settings

    return ReadingBuffer(
        max_items=getattr(settings, "IOT_BUFFER_MAX_ITEMS", 20),
        max_age_sec=getattr(settings, "IOT_BUFFER_MAX_AGE_SEC", 4.0),
    )


#: Process-wide singleton. One uvicorn worker only -- see render.yaml.
BUFFER = _build_buffer()

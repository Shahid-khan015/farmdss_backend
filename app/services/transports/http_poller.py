# Follows pattern from: app/main.py (@app.on_event startup hooks), app/config.py (settings)
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models.session import OperationSession
from app.services.ingest_buffer import BUFFER
from app.services.ingestion_pipeline import ATTACHABLE_SESSION_STATUSES, ingest_normalized_batch
from app.services.session_gate import GATE
from app.services.normalizer import FEEDS, NormalizedReading, adafruit_slug_for_feed_key, process_iot_data

logger = logging.getLogger(__name__)

ADAFRUIT_DATA_BASE = "https://io.adafruit.com/api/v2"
_PER_REQUEST_TIMEOUT_SEC = 15.0
_MAX_ATTEMPTS = 3


@dataclass
class IngestionStatus:
    """Live poller state, surfaced by GET /health/iot so a silent pipeline can be diagnosed."""

    started_at: Optional[str] = None
    last_cycle_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_error: Optional[str] = None
    last_cycle_ms: Optional[int] = None
    last_cycle_fetched: int = 0
    last_cycle_stored: int = 0
    last_cycle_feeds_ok: int = 0
    last_cycle_feeds_failed: List[str] = field(default_factory=list)
    #: Feeds that exist but hold no data points yet. Reported separately from failures
    #: so a never-written feed is not a permanent false alarm.
    last_cycle_feeds_empty: List[str] = field(default_factory=list)
    cycles_total: int = 0
    consecutive_failures: int = 0
    current_interval_sec: Optional[float] = None
    session_active: bool = False
    last_fetch_through_at: Optional[str] = None
    fetch_through_total: int = 0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "last_cycle_at": self.last_cycle_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "last_cycle_ms": self.last_cycle_ms,
            "last_cycle_fetched": self.last_cycle_fetched,
            "last_cycle_stored": self.last_cycle_stored,
            "last_cycle_feeds_ok": self.last_cycle_feeds_ok,
            "last_cycle_feeds_failed": list(self.last_cycle_feeds_failed),
            "last_cycle_feeds_empty": list(self.last_cycle_feeds_empty),
            "cycles_total": self.cycles_total,
            "consecutive_failures": self.consecutive_failures,
            "current_interval_sec": self.current_interval_sec,
            "session_active": self.session_active,
            "last_fetch_through_at": self.last_fetch_through_at,
            "fetch_through_total": self.fetch_through_total,
        }


STATUS = IngestionStatus()

# One client for the process: reconnecting and re-negotiating TLS for every feed on every cycle
# is the bulk of a cycle's wall time when the API is far from Adafruit.
_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(
                base_url=ADAFRUIT_DATA_BASE,
                timeout=httpx.Timeout(_PER_REQUEST_TIMEOUT_SEC),
                limits=httpx.Limits(max_connections=12, max_keepalive_connections=12),
                headers={"X-AIO-Key": settings.AIO_KEY} if settings.AIO_KEY else None,
            )
        return _client


def close_client() -> None:
    global _client
    with _client_lock:
        if _client is not None and not _client.is_closed:
            try:
                _client.close()
            except Exception:  # pragma: no cover - shutdown best effort
                pass
        _client = None


def _fetch_feed_rows(
    client: httpx.Client,
    *,
    username: str,
    key: str,
    feed_key: str,
    limit: int,
    deadline: Optional[float] = None,
) -> Optional[List[Any]]:
    """
    Fetch recent data points for one feed.

    Returns the rows, or ``None`` when the fetch genuinely failed. That distinction
    matters: an empty list is a legitimate answer for a feed that exists but has never
    been written to, and conflating the two makes such a feed show up as "failed" on
    every cycle forever -- a permanent false alarm that teaches people to ignore the
    diagnostic. (`machine-status` on the reference account is exactly this case.)

    Retries are bounded by ``deadline`` (a ``time.monotonic()`` instant) so a single unhealthy
    feed degrades its own freshness instead of stalling the whole cycle behind it.
    """
    slug = adafruit_slug_for_feed_key(feed_key)
    url = f"/{username}/feeds/{slug}/data"
    last_exc: Optional[Exception] = None

    for attempt in range(_MAX_ATTEMPTS):
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            logger.warning("Adafruit fetch budget exhausted before feed=%s", feed_key)
            break
        timeout = _PER_REQUEST_TIMEOUT_SEC if remaining is None else min(_PER_REQUEST_TIMEOUT_SEC, remaining)

        try:
            response = client.get(
                url,
                params={"limit": limit},
                headers={"X-AIO-Key": key},
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            logger.warning(
                "Adafruit HTTP fetch failed feed=%s attempt=%s: %s", feed_key, attempt + 1, exc
            )
            if attempt == _MAX_ATTEMPTS - 1:
                break
            backoff = 0.5 * (attempt + 1)
            if deadline is not None and time.monotonic() + backoff >= deadline:
                break
            time.sleep(backoff)

    if last_exc:
        logger.error("Adafruit HTTP fetch gave up feed=%s: %s", feed_key, last_exc)
    return None


def fetch_all_feeds(
    *,
    limit: Optional[int] = None,
    budget_sec: Optional[float] = None,
) -> Tuple[List[NormalizedReading], List[str]]:
    """
    Fetch every configured feed concurrently and normalize the rows.

    Returns (normalized_readings, failed_feed_keys). The feeds are independent, so issuing them
    in parallel turns ~10 serial round trips into roughly one.
    """
    if not settings.AIO_USERNAME or not settings.AIO_KEY:
        logger.warning("Adafruit fetch skipped: AIO_USERNAME / AIO_KEY not configured")
        return [], list(FEEDS)

    row_limit = int(settings.IOT_HTTP_POLL_LIMIT if limit is None else limit)
    budget = float(settings.IOT_POLL_CYCLE_BUDGET_SEC if budget_sec is None else budget_sec)
    deadline = time.monotonic() + budget
    client = get_client()

    def _one(feed_key: str) -> Tuple[str, Optional[List[Any]]]:
        try:
            return feed_key, _fetch_feed_rows(
                client,
                username=settings.AIO_USERNAME,
                key=settings.AIO_KEY,
                feed_key=feed_key,
                limit=row_limit,
                deadline=deadline,
            )
        except Exception:
            # An unexpected error on one feed must not abort the whole cycle.
            logger.exception("Adafruit fetch raised unexpectedly feed=%s", feed_key)
            return feed_key, None

    results: List[Tuple[str, Optional[List[Any]]]] = []
    with ThreadPoolExecutor(max_workers=min(len(FEEDS), 10), thread_name_prefix="aio-fetch") as pool:
        for outcome in pool.map(_one, list(FEEDS)):
            results.append(outcome)

    batch: List[NormalizedReading] = []
    failed: List[str] = []
    empty: List[str] = []
    for feed_key, rows in results:
        if rows is None:
            failed.append(feed_key)
            continue
        if not rows:
            # The feed exists but has no data points yet. Not an error.
            empty.append(feed_key)
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized = process_iot_data(
                feed_key, row, default_device_id=settings.IOT_DEFAULT_DEVICE_ID
            )
            if normalized is not None:
                batch.append(normalized)

    STATUS.last_cycle_feeds_empty = empty
    if empty:
        logger.info(
            "Adafruit feeds with no data points yet (not an error): %s", ", ".join(sorted(empty))
        )
    return batch, failed


def store_readings(batch: List[NormalizedReading]) -> int:
    if not batch:
        return 0
    db = SessionLocal()
    try:
        return ingest_normalized_batch(db, batch)
    finally:
        db.close()


def poll_once(*, limit: Optional[int] = None, budget_sec: Optional[float] = None) -> int:
    """Single poll across all configured feeds. Returns number of new rows stored."""
    if not settings.AIO_USERNAME or not settings.AIO_KEY:
        logger.warning("IoT HTTP poll skipped: missing AIO_USERNAME / AIO_KEY")
        return 0
    batch, _failed = fetch_all_feeds(limit=limit, budget_sec=budget_sec)
    return store_readings(batch)


def has_attachable_session() -> bool:
    """True when an operator is running (or has paused) a GPS-tracked session."""
    db = SessionLocal()
    try:
        found = db.scalars(
            select(OperationSession.id)
            .where(
                OperationSession.status.in_(ATTACHABLE_SESSION_STATUSES),
                OperationSession.gps_tracking_enabled.is_(True),
            )
            .limit(1)
        ).first()
        return found is not None
    except Exception:
        logger.exception("Session-state check failed; assuming a session is active")
        return True
    finally:
        db.close()


def choose_interval(session_active: bool, active_interval: float, idle_interval: float) -> float:
    """Hot cadence while an operation is running, slow cadence otherwise."""
    return active_interval if session_active else max(active_interval, idle_interval)


def run_http_poller_loop(stop: threading.Event, interval_sec: float) -> None:
    """
    Blocking loop for a daemon thread; stops when `stop` is set.

    **Backfill, not the hot path.** With the MQTT subscriber pushing readings as they
    arrive, this loop exists to catch what MQTT missed (a dropped connection, a message
    lost while the process was restarting). It therefore runs at the much slower
    `IOT_BACKFILL_POLL_INTERVAL_SEC` so the two transports stop competing for the same
    data points -- they converge on one row either way now that MQTT carries Adafruit's
    real record id, but there is no reason to fetch twice.

    **Fully dormant when no session is running.** The loop parks on the session gate
    rather than polling at a slow idle cadence: no Adafruit request, no database query,
    no connection held. That is what lets the database compute suspend between sessions.

    It also owns the buffer's flush timer -- it is already a ticking loop, so the
    micro-batch needs no thread of its own.
    """
    active_interval = max(3.0, float(interval_sec))
    idle_interval = max(active_interval, float(settings.IOT_IDLE_POLL_INTERVAL_SEC))
    recheck = float(getattr(settings, "IOT_GATE_RECHECK_SEC", 30.0))

    STATUS.started_at = _now_iso()
    logger.info(
        "IoT poller loop starting (backfill=%ss idle=%ss feeds=%s limit=%s)",
        active_interval,
        idle_interval,
        len(FEEDS),
        settings.IOT_HTTP_POLL_LIMIT,
    )

    while not stop.is_set():
        cycle_start = time.monotonic()
        interval = active_interval

        # Flush regardless of gate state: readings can still be buffered from a session
        # that has just ended.
        try:
            BUFFER.flush_if_due()
        except Exception:
            logger.exception("buffer flush from poller loop failed")

        if not GATE.is_open():
            STATUS.session_active = False
            STATUS.current_interval_sec = None
            GATE.wait_for_change(timeout=recheck)
            continue

        try:
            session_active = True
            interval = choose_interval(session_active, active_interval, idle_interval)
            STATUS.session_active = session_active
            STATUS.current_interval_sec = interval

            batch, failed = fetch_all_feeds(budget_sec=min(settings.IOT_POLL_CYCLE_BUDGET_SEC, interval * 3))
            stored = store_readings(batch)

            elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
            STATUS.last_cycle_at = _now_iso()
            STATUS.last_cycle_ms = elapsed_ms
            STATUS.last_cycle_fetched = len(batch)
            STATUS.last_cycle_stored = stored
            STATUS.last_cycle_feeds_ok = len(FEEDS) - len(failed)
            STATUS.last_cycle_feeds_failed = failed
            STATUS.cycles_total += 1
            STATUS.consecutive_failures = 0
            STATUS.last_error = None
            STATUS.last_success_at = STATUS.last_cycle_at

            logger.info(
                "IoT poller cycle: feeds=%s/%s fetched=%s stored=%s session_active=%s ms=%s%s",
                STATUS.last_cycle_feeds_ok,
                len(FEEDS),
                len(batch),
                stored,
                session_active,
                elapsed_ms,
                (" failed=" + ",".join(failed)) if failed else "",
            )
        except Exception as exc:
            STATUS.cycles_total += 1
            STATUS.consecutive_failures += 1
            STATUS.last_error = "{}: {}".format(type(exc).__name__, exc)
            STATUS.last_cycle_at = _now_iso()
            logger.exception("IoT HTTP poll cycle failed")

        # Keep a steady cadence even when a cycle runs long.
        remaining = interval - (time.monotonic() - cycle_start)
        if stop.wait(timeout=max(0.5, remaining)):
            break

    close_client()
    try:
        BUFFER.flush()
    except Exception:
        logger.exception("final buffer flush on poller shutdown failed")
    logger.info("IoT poller loop stopped after %s cycle(s)", STATUS.cycles_total)

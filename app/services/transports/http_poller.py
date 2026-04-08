# Follows pattern from: app/main.py (@app.on_event startup hooks), app/config.py (settings)
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from app.config import settings
from app.database import SessionLocal
from app.services.ingestion_pipeline import ingest_normalized_batch
from app.services.normalizer import FEEDS, NormalizedReading, adafruit_slug_for_feed_key, process_iot_data

logger = logging.getLogger(__name__)

ADAFRUIT_DATA_BASE = "https://io.adafruit.com/api/v2"


def _fetch_feed_rows(
    client: httpx.Client,
    *,
    username: str,
    key: str,
    feed_key: str,
    limit: int,
) -> list[Any]:
    slug = adafruit_slug_for_feed_key(feed_key)
    url = f"{ADAFRUIT_DATA_BASE}/{username}/feeds/{slug}/data"
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = client.get(
                url,
                params={"limit": limit},
                headers={"X-AIO-Key": key},
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, list) else []
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            logger.warning(
                "Adafruit HTTP fetch failed feed=%s attempt=%s: %s",
                feed_key,
                attempt + 1,
                exc,
            )
            time.sleep(1.0 * (attempt + 1))
    if last_exc:
        logger.error("Adafruit HTTP fetch exhausted retries feed=%s: %s", feed_key, last_exc)
    return []


def poll_once() -> int:
    """Single poll across all configured feeds. Returns number of new rows stored."""
    if not settings.AIO_USERNAME or not settings.AIO_KEY:
        logger.debug("IoT HTTP poll skipped: missing AIO_USERNAME / AIO_KEY")
        return 0

    batch: list[NormalizedReading] = []
    with httpx.Client() as client:
        for fk in FEEDS:
            rows = _fetch_feed_rows(
                client,
                username=settings.AIO_USERNAME,
                key=settings.AIO_KEY,
                feed_key=fk,
                limit=settings.IOT_HTTP_POLL_LIMIT,
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                normalized = process_iot_data(
                    fk,
                    row,
                    default_device_id=settings.IOT_DEFAULT_DEVICE_ID,
                )
                if normalized is not None:
                    batch.append(normalized)

    if not batch:
        return 0

    db = SessionLocal()
    try:
        return ingest_normalized_batch(db, batch)
    finally:
        db.close()


def run_http_poller_loop(stop: threading.Event, interval_sec: float) -> None:
    """Blocking loop for a daemon thread; stops when `stop` is set."""
    while not stop.is_set():
        try:
            inserted = poll_once()
            if inserted:
                logger.debug("IoT HTTP poll stored %s new reading(s)", inserted)
        except Exception:
            logger.exception("IoT HTTP poll cycle failed")
        if stop.wait(timeout=interval_sec):
            break

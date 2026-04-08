#!/usr/bin/env python3
"""
Direct IoT transport mock: Adafruit-shaped REST rows → process_iot_data → ingest_reading.

Same pipeline as:
  Adafruit HTTP `/feeds/{slug}/data` → http_poller → process_iot_data → ingest_normalized_batch

This script skips HTTP and builds synthetic rows identical in shape to poller input, then
calls the normalizer and ingestion pipeline in-process (no new routes, no DB bypass).

Run (package root = backend/):

  cd backend
  .venv\\Scripts\\python.exe scripts/mock_iot_direct.py

Environment:
  MOCK_INTERVAL_SECONDS — default 5
  MOCK_DEVICE_ID — default 'default'
  DATABASE_URL — PostgreSQL only, same as the FastAPI app (via env or backend/.env; required unless --dry-run)

This simulator expects a live PostgreSQL database (same migrations as production). SQLite URLs are rejected
so the script never targets a local sqlite file by mistake.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

# ---------------------------------------------------------------------------
# Resolve backend package root (directory containing `app/`)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _SCRIPT_DIR.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Load env before any `app` import so `app.database` / Settings see DATABASE_URL (same as API).
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env", override=False)
except ImportError:
    pass

from app.services.normalizer import FEEDS

logger = logging.getLogger("mock_iot_direct")

# Same order as http_poller: for fk in FEEDS
_FEED_KEYS: Final[tuple[str, ...]] = tuple(FEEDS.keys())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_postgres_database_url(url: str) -> bool:
    """True for URLs SQLAlchemy uses with PostgreSQL (not SQLite)."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    # postgresql://, postgresql+psycopg2://, postgres://, etc.
    return u.startswith("postgres://") or u.startswith("postgresql")


@dataclass
class SensorState:
    """Realistic evolving sensor state: random walks, spikes, GPS zigzag, machine toggles."""

    soil_moisture: float = 38.0
    gearbox_temp: float = 72.0
    forward_speed: float = 6.5
    depth_of_operation: float = 14.0
    pto_shaft_speed: int = 540
    vibration: float = 1.1
    wheel_slip: float = 6.0
    machine_running: bool = True
    lat: float = 28.6139
    lon: float = 77.2090
    zigzag_i: int = 0
    tick: int = 0

    _rng: random.Random = field(default_factory=random.Random)

    def step(self) -> None:
        self.tick += 1
        self.zigzag_i += 1
        r = self._rng

        # Random walk — soil moisture & gearbox temp
        self.soil_moisture = max(12.0, min(78.0, self.soil_moisture + r.gauss(0.0, 0.55)))
        self.gearbox_temp = max(40.0, min(110.0, self.gearbox_temp + r.gauss(0.0, 0.35)))

        # Periodic / probabilistic spikes (alerts)
        if self.tick % 41 == 0 or r.random() < 0.02:
            self.gearbox_temp = r.choice([88.5, 91.0, 102.5])
        if self.tick % 37 == 0 or r.random() < 0.025:
            self.vibration = r.uniform(3.2, 4.8)
        else:
            self.vibration = max(0.05, min(2.8, self.vibration + r.gauss(0.0, 0.12)))
        if self.tick % 43 == 0 or r.random() < 0.02:
            self.wheel_slip = r.uniform(16.0, 28.0)
        else:
            self.wheel_slip = max(0.0, min(22.0, self.wheel_slip + r.gauss(0.0, 0.8)))

        # Machine RUNNING / IDLE
        if r.random() < 0.08 or self.tick % 25 == 0:
            self.machine_running = not self.machine_running

        if self.machine_running:
            self.forward_speed = max(0.5, min(14.0, self.forward_speed + r.gauss(0.0, 0.4)))
            self.pto_shaft_speed = r.choice([540, 540, 1000])
            self.depth_of_operation = max(5.0, min(28.0, self.depth_of_operation + r.gauss(0.0, 0.25)))
        else:
            self.forward_speed = max(0.0, min(2.0, self.forward_speed * 0.85))
            self.pto_shaft_speed = 0

        # GPS zigzag (small step pattern)
        step = 0.00012
        if self.zigzag_i % 4 < 2:
            self.lat += step * r.uniform(0.7, 1.3)
            self.lon += step * r.uniform(-0.4, 0.4)
        else:
            self.lat += step * r.uniform(-0.4, 0.4)
            self.lon -= step * r.uniform(0.7, 1.3)


def _slug_for(canonical: str) -> str:
    from app.services.normalizer import adafruit_slug_for_feed_key

    return adafruit_slug_for_feed_key(canonical)


def _base_row(
    *,
    canonical_feed: str,
    value: str,
    device_id: str,
    now: datetime,
    lat: float | None,
    lon: float | None,
) -> dict[str, Any]:
    slug = _slug_for(canonical_feed)
    created_epoch = int(now.timestamp())
    created_at = _iso_z(now)
    # Globally unique adafruit_id (dedup key); avoids collisions across ticks/processes.
    rid = f"MOCK-{slug}-{uuid.uuid4()}"
    return {
        "id": rid,
        "value": value,
        "feed_key": slug,
        "created_at": created_at,
        "created_epoch": created_epoch,
        "lat": lat,
        "lon": lon,
        "device_id": device_id,
    }


def build_records(state: SensorState, device_id: str) -> list[dict[str, Any]]:
    """Build 10 raw REST rows in canonical feed order (same as HTTP poller batch order)."""
    now = _utc_now()
    speed = state.forward_speed
    depth = state.depth_of_operation
    fc = speed * depth * 0.001

    gps_payload = json.dumps(
        {"lat": state.lat, "lon": state.lon, "ele": 215.0},
    )
    status = "RUNNING" if state.machine_running else "IDLE"
    rows: list[dict[str, Any]] = [
        _base_row(
            canonical_feed="soil_moisture",
            value=f"{state.soil_moisture:.2f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="position_tracking",
            value=gps_payload,
            device_id=device_id,
            now=now,
            lat=state.lat,
            lon=state.lon,
        ),
        _base_row(
            canonical_feed="forward_speed",
            value=f"{speed:.2f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="pto_shaft_speed",
            value=str(int(state.pto_shaft_speed)),
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="depth_of_operation",
            value=f"{depth:.2f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="machine_status",
            value=status,
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="gearbox_temperature",
            value=f"{state.gearbox_temp:.1f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="vibration",
            value=f"{state.vibration:.3f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="wheel_slip",
            value=f"{state.wheel_slip:.2f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
        _base_row(
            canonical_feed="field_capacity",
            value=f"{fc:.4f}",
            device_id=device_id,
            now=now,
            lat=None,
            lon=None,
        ),
    ]

    if len(rows) != len(FEEDS):
        raise RuntimeError("build_records: feed count mismatch")
    return rows


def ingest_batch(
    records: list[dict[str, Any]],
    *,
    device_id: str,
) -> tuple[int, int]:
    from app.database import SessionLocal
    from app.services.ingestion_pipeline import ingest_reading
    from app.services.normalizer import process_iot_data

    inserted = 0
    skipped = 0
    errors = 0

    if len(records) != len(_FEED_KEYS):
        raise ValueError("ingest_batch expects len(records) == len(FEEDS)")

    for fk, raw in zip(_FEED_KEYS, records):
        # Fresh session per record: isolates FK/integrity errors
        db = SessionLocal()
        try:
            normalized = process_iot_data(fk, raw, default_device_id=device_id)
            if normalized is None:
                logger.warning("process_iot_data returned None for feed_key=%s", fk)
                skipped += 1
                continue
            ok, _row = ingest_reading(db, normalized, commit=True)
            if ok:
                inserted += 1
            else:
                skipped += 1
        except Exception as exc:
            errors += 1
            logger.error(
                "Failed to ingest feed_key=%s adafruit_id=%s: %s",
                fk,
                raw.get("id", "?"),
                exc,
            )
        finally:
            db.close()

    if errors > 0:
        logger.warning(
            "Tick completed with %d errors. Check FK constraints and DB connection.",
            errors,
        )

    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Mock Adafruit IoT rows → process_iot_data → ingest_reading (same as HTTP poller). "
            "Requires PostgreSQL (DATABASE_URL); SQLite is not supported."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads only; do not connect to the database",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick and exit",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("MOCK_LOG_LEVEL", "INFO"),
        help="Logging level (default INFO)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    interval = float(os.environ.get("MOCK_INTERVAL_SECONDS", "5"))
    device_id = os.environ.get("MOCK_DEVICE_ID", "default")
    dry_run = bool(args.dry_run) or os.environ.get("MOCK_DRY_RUN", "").lower() in (
        "1",
        "true",
        "yes",
    )

    if not dry_run:
        from app.config import settings

        if not _is_postgres_database_url(settings.DATABASE_URL):
            print(
                "ERROR: mock_iot_direct is PostgreSQL-only. "
                f"settings.DATABASE_URL must be a PostgreSQL URL (got {settings.DATABASE_URL!r}). "
                "Set DATABASE_URL in the environment or backend/.env to match the API, or use --dry-run.",
                file=sys.stderr,
            )
            sys.exit(1)

    state = SensorState()
    tick_n = 0

    while True:
        tick_n += 1
        state.step()
        records = build_records(state, device_id)

        ts = _iso_z(_utc_now())
        if dry_run:
            for fk, raw in zip(_FEED_KEYS, records):
                print(f"[{ts}] dry-run {fk}: {json.dumps(raw, default=str)[:220]}")
            print(
                f"[{ts}] Tick {tick_n} | Inserted n/a | Skipped n/a | "
                f"Speed {state.forward_speed:.2f} | Depth {state.depth_of_operation:.2f} | "
                f"Temp {state.gearbox_temp:.1f}",
            )
        else:
            ins, skip = ingest_batch(records, device_id=device_id)
            total = len(records)
            status = "OK" if ins > 0 else "WARN: no inserts"
            print(
                f"[{ts}] Tick {tick_n} | {status} | "
                f"Inserted {ins}/{total} | Skipped {skip} | "
                f"Speed {state.forward_speed:.2f} km/h | "
                f"Depth {state.depth_of_operation:.2f} cm | "
                f"Temp {state.gearbox_temp:.1f}\xb0C | "
                f"Machine {'RUNNING' if state.machine_running else 'IDLE'}",
            )

        if args.once:
            break
        time.sleep(max(0.5, interval))


if __name__ == "__main__":
    main()

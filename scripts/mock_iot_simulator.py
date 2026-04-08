#!/usr/bin/env python3
"""
Standalone IoT simulator for local development and production-style Adafruit forwarding.

This script does NOT call a separate HTTP ingest route: the backend ingests IoT only
in-process (HTTP poller / MQTT → normalizer → ingest_reading). To avoid changing
backend code, this tool uses the **same** Python pipeline:

  Adafruit-shaped dict → process_iot_data() → ingest_reading() → database

Mock mode:  builds synthetic rows matching Adafruit IO REST `data` item shape.
Adafruit mode: GETs `/api/v2/{username}/feeds/{slug}/data` (same as http_poller).

Run (this file lives in `backend/scripts/`):

  cd /path/to/newdss/backend
  . .venv/Scripts/activate          # Windows PowerShell: .venv\\Scripts\\Activate.ps1
  python scripts/mock_iot_simulator.py --help

  # From monorepo root:
  python backend/scripts/mock_iot_simulator.py --help

  # Optional extra env file (must appear before other flags if you use the pre-parser):
  python scripts/mock_iot_simulator.py --load-env my.env --mode mock --dry-run

Environment: values are read from `backend/scripts/.env`, then `backend/.env`, without
putting simulator-only keys into the process environment (avoids breaking FastAPI Settings).

See `backend/scripts/.env.example`.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Resolve backend package (no install step — repo layout)
# Script path: <repo>/backend/scripts/mock_iot_simulator.py
#   -> parent = backend/scripts, parent.parent = backend (package root)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _SCRIPT_DIR.parent
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

logger = logging.getLogger("mock_iot_simulator")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_bool(s: str | None, default: bool = False) -> bool:
    if s is None or s == "":
        return default
    return s.strip().lower() in ("1", "true", "yes", "on")


# Keys allowed into os.environ — must match `app.config.Settings` only. Simulator-only
# variables (IOT_SIMULATOR_*, BACKEND_BASE_URL, DRY_RUN, …) must NEVER be dumped into
# the environment or Pydantic Settings() will raise "extra_forbidden".
_BACKEND_SETTINGS_ENV_KEYS = frozenset(
    {
        "DATABASE_URL",
        "SECRET_KEY",
        "JWT_SECRET_KEY",
        "DEBUG",
        "API_V1_PREFIX",
        "PROJECT_NAME",
        "CORS_ORIGINS",
        "AIO_USERNAME",
        "AIO_KEY",
        "IOT_DEFAULT_DEVICE_ID",
        "IOT_HTTP_POLL_INTERVAL_SEC",
        "IOT_HTTP_POLL_LIMIT",
        "ENABLE_IOT_HTTP_POLLER",
        "ENABLE_IOT_MQTT",
        "IOT_MQTT_BROKER",
        "IOT_MQTT_PORT",
    }
)


def _read_dotenv_mapping(path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("Install python-dotenv: pip install python-dotenv", file=sys.stderr)
        sys.exit(1)
    raw = dotenv_values(path)
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v is not None and str(v).strip() != "":
            out[k] = str(v).strip()
    return out


def _merge_dotenv(cli_env: Path | None) -> dict[str, str]:
    """Merge .env files; later files override. Does not call load_dotenv (no os.environ pollution)."""
    merged: dict[str, str] = {}
    for path in (
        _SCRIPT_DIR / ".env",
        _BACKEND_ROOT / ".env",
        _REPO_ROOT / "scripts" / ".env",
    ):
        if path.is_file():
            merged.update(_read_dotenv_mapping(path))
    if cli_env is not None and cli_env.is_file():
        merged.update(_read_dotenv_mapping(cli_env))
    return merged


def _apply_backend_settings_env(merged: dict[str, str]) -> None:
    """Copy only Settings-compatible keys into os.environ so `from app.config import settings` works."""
    for key in _BACKEND_SETTINGS_ENV_KEYS:
        if key in merged:
            os.environ.setdefault(key, merged[key])


def _env(
    merged: dict[str, str],
    key: str,
    default: str | None = None,
) -> str | None:
    """Simulator options: merged .env first, then process environment."""
    if key in merged:
        return merged[key]
    return os.environ.get(key, default)


# --- Adafruit REST (same contract as app/services/transports/http_poller.py) ---
ADAFRUIT_DATA_BASE = "https://io.adafruit.com/api/v2"


def _fetch_feed_rows_adafruit(
    client: Any,
    *,
    username: str,
    key: str,
    slug: str,
    limit: int,
) -> list[dict[str, Any]]:
    import httpx

    url = f"{ADAFRUIT_DATA_BASE}/{username}/feeds/{slug}/data"
    response = client.get(
        url,
        params={"limit": limit},
        headers={"X-AIO-Key": key},
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Mock data: same keys as Adafruit IO `/feeds/{slug}/data` list items
# ---------------------------------------------------------------------------
def _mock_row_for_feed(feed_key: str, *, device_id: str, now: datetime) -> dict[str, Any]:
    """Build one synthetic API row per feed; `id` must be unique for DB dedup."""
    aid = str(uuid.uuid4())
    created_epoch = _epoch(now)
    created_at = _iso(now)
    expiration = _iso(datetime.fromtimestamp(created_epoch + 86400 * 30, tz=timezone.utc))

    base: dict[str, Any] = {
        "id": aid,
        "feed_id": random.randint(100_000, 999_999),
        "feed_key": _slug_for_feed_key(feed_key),
        "group_id": None,
        "expiration": expiration,
        "created_at": created_at,
        "created_epoch": int(created_epoch),
        "device_id": device_id,
    }

    if feed_key == "soil_moisture":
        base["value"] = f"{random.uniform(18.0, 55.0):.2f}"
        base["lat"] = None
        base["lon"] = None
        base["ele"] = None
        base["location"] = None
    elif feed_key == "position_tracking":
        lat = 28.6 + random.uniform(-0.05, 0.05)
        lon = 77.2 + random.uniform(-0.05, 0.05)
        base["value"] = json.dumps({"lat": lat, "lon": lon, "ele": 200.0})
        base["lat"] = lat
        base["lon"] = lon
        base["ele"] = 200.0
        base["location"] = None
    elif feed_key == "forward_speed":
        base["value"] = f"{random.uniform(0.0, 12.0):.2f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "pto_shaft_speed":
        base["value"] = f"{random.choice([0, 540, 1000]):d}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "depth_of_operation":
        base["value"] = f"{random.uniform(5.0, 25.0):.2f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "machine_status":
        base["value"] = random.choice(["idle", "working", "transport"])
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "gearbox_temperature":
        base["value"] = f"{random.uniform(45.0, 95.0):.1f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "vibration":
        base["value"] = f"{random.uniform(0.1, 3.0):.3f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "wheel_slip":
        base["value"] = f"{random.uniform(0.0, 25.0):.2f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    elif feed_key == "field_capacity":
        base["value"] = f"{random.uniform(0.5, 8.0):.2f}"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None
    else:
        base["value"] = "0"
        base["lat"] = base["lon"] = base["ele"] = base["location"] = None

    return base


def _slug_for_feed_key(feed_key: str) -> str:
    """Match adafruit_slug_for_feed_key without importing before path is set."""
    from app.services.normalizer import adafruit_slug_for_feed_key

    return adafruit_slug_for_feed_key(feed_key)


def _run_cycle(
    *,
    mode: str,
    device_id: str,
    dry_run: bool,
    aio_username: str,
    aio_key: str,
    poll_limit: int,
) -> tuple[int, list[tuple[str, dict[str, Any], str]]]:
    """
    Returns (inserted_count, log_lines) where log_lines are (feed_key, raw_record, status).
    """
    from app.services.ingestion_pipeline import ingest_reading
    from app.services.normalizer import FEEDS, process_iot_data
    from app.database import SessionLocal

    inserted_total = 0
    log: list[tuple[str, dict[str, Any], str]] = []

    if mode == "mock":
        now = _utc_now()
        rows_by_key: dict[str, dict[str, Any]] = {}
        for fk in FEEDS:
            rows_by_key[fk] = _mock_row_for_feed(fk, device_id=device_id, now=now)
    else:
        import httpx

        if not aio_username or not aio_key:
            raise RuntimeError("Adafruit mode requires AIO_USERNAME and AIO_KEY in the environment.")
        rows_by_key = {}
        with httpx.Client() as client:
            for fk in FEEDS:
                slug = _slug_for_feed_key(fk)
                data = _fetch_feed_rows_adafruit(
                    client,
                    username=aio_username,
                    key=aio_key,
                    slug=slug,
                    limit=poll_limit,
                )
                if not data:
                    log.append((fk, {}, "no_rows_from_adafruit"))
                    continue
                # Take newest first (Adafruit returns newest-first typically)
                row = data[0] if isinstance(data[0], dict) else {}
                if isinstance(row, dict):
                    row = dict(row)
                    row.setdefault("device_id", device_id)
                rows_by_key[fk] = row

    if dry_run:
        for fk, raw in rows_by_key.items():
            norm = process_iot_data(fk, raw, default_device_id=device_id)
            if norm is None:
                log.append((fk, raw, "dry_run: normalizer returned None"))
                continue
            prev = norm.raw_value
            preview = (prev[:80] + "...") if len(prev) > 80 else prev
            log.append(
                (
                    fk,
                    raw,
                    f"dry_run would_ingest adafruit_id={norm.adafruit_id} value_preview={preview!r}",
                )
            )
        return 0, log

    db = SessionLocal()
    try:
        for fk, raw in rows_by_key.items():
            if not raw:
                continue
            normalized = process_iot_data(fk, raw, default_device_id=device_id)
            if normalized is None:
                log.append((fk, raw, "skipped: normalizer returned None"))
                continue
            inserted, _ = ingest_reading(db, normalized, commit=True)
            if inserted:
                inserted_total += 1
                log.append((fk, raw, f"inserted ok adafruit_id={normalized.adafruit_id}"))
            else:
                log.append((fk, raw, f"duplicate skipped adafruit_id={normalized.adafruit_id}"))
    finally:
        db.close()

    return inserted_total, log


def _verify_latest(base_url: str, device_id: str, token: str | None) -> None:
    import httpx

    url = f"{base_url.rstrip('/')}/api/v1/iot/latest"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client() as client:
        r = client.get(url, params={"device_id": device_id}, headers=headers, timeout=15.0)
        r.raise_for_status()
        data = r.json()
    feeds = data.get("feeds") or []
    print(f"[verify] GET {url} -> {len(feeds)} feed slots (device_id={device_id})")
    for f in feeds[:5]:
        fk = f.get("feed_key")
        nv = f.get("numeric_value")
        ts = f.get("device_timestamp")
        print(f"       {fk}: numeric={nv} @ {ts}")
    if len(feeds) > 5:
        print(f"       ... +{len(feeds) - 5} more")


def main() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--load-env", type=Path, default=None, dest="load_env_path")
    pre_ns, remaining = pre.parse_known_args()

    merged = _merge_dotenv(pre_ns.load_env_path)
    _apply_backend_settings_env(merged)

    def _def(key: str, fallback: str) -> str:
        v = _env(merged, key, fallback)
        return v if v is not None else fallback

    parser = argparse.ArgumentParser(
        description="Mock or Adafruit IoT data → same backend ingest pipeline as the HTTP poller.",
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "adafruit"),
        default=_def("IOT_SIMULATOR_MODE", "mock"),
        help="mock: synthetic Adafruit-shaped rows; adafruit: fetch from Adafruit IO",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=float(_def("IOT_SIMULATOR_INTERVAL_SEC", "5")),
        help="Seconds to wait between cycles",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=int(_def("IOT_SIMULATOR_CYCLES", "1")),
        help="Number of cycles (0 = infinite)",
    )
    parser.add_argument(
        "--device-id",
        default=_def("IOT_DEFAULT_DEVICE_ID", "default"),
        help="device_id on each reading",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payloads only; no DATABASE_URL required",
    )
    parser.add_argument(
        "--poll-limit",
        type=int,
        default=int(_def("IOT_HTTP_POLL_LIMIT", "5")),
        help="Adafruit: max data points per feed per request",
    )
    parser.add_argument(
        "--backend-url",
        default=_def("BACKEND_BASE_URL", "http://127.0.0.1:8000"),
        help="Base URL for optional GET /api/v1/iot/latest verification",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Disable GET latest after each cycle",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Less console output",
    )
    args = parser.parse_args(remaining)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    if merged:
        print(f"[env] Loaded {len(merged)} entries from .env (backend settings applied selectively)")

    dry_run = bool(args.dry_run) or _parse_bool(_env(merged, "DRY_RUN"), False)
    verify = not args.no_verify and _parse_bool(_env(merged, "IOT_VERIFY_LATEST"), True)

    if not dry_run:
        if not os.environ.get("DATABASE_URL"):
            print(
                "ERROR: DATABASE_URL is not set. Set it to the same value as the FastAPI app, "
                "or use --dry-run.",
                file=sys.stderr,
            )
            sys.exit(1)

    aio_user = os.environ.get("AIO_USERNAME", "") or _env(merged, "AIO_USERNAME", "") or ""
    aio_key = os.environ.get("AIO_KEY", "") or _env(merged, "AIO_KEY", "") or ""
    token = (_env(merged, "ACCESS_TOKEN", "") or "").strip() or None

    cycle = 0
    while True:
        cycle += 1
        print(f"\n=== Cycle {cycle} mode={args.mode} device_id={args.device_id} ===")

        try:
            n, log = _run_cycle(
                mode=args.mode,
                device_id=args.device_id,
                dry_run=dry_run,
                aio_username=aio_user,
                aio_key=aio_key,
                poll_limit=args.poll_limit,
            )
        except Exception as exc:
            print(f"[error] cycle failed: {exc}", file=sys.stderr)
            if args.mode == "adafruit":
                print("        Check AIO_USERNAME, AIO_KEY, and network.", file=sys.stderr)
            raise

        for fk, raw, status in log:
            rid = raw.get("id", "?") if raw else "?"
            preview = ""
            if raw.get("value") is not None:
                v = raw["value"]
                s = v if isinstance(v, str) else json.dumps(v)
                preview = (s[:60] + "...") if len(s) > 60 else s
            print(f"  {fk}: id={rid} value={preview!r} -> {status}")

        if not dry_run:
            print(f"  Summary: {n} new row(s) inserted this cycle (duplicates skipped by adafruit_id).")

        if verify and not dry_run:
            try:
                _verify_latest(args.backend_url, args.device_id, token)
            except Exception as ve:
                print(f"  [verify] skipped or failed: {ve}")

        if args.cycles != 0 and cycle >= args.cycles:
            break
        if args.cycles == 0 or cycle < args.cycles:
            time.sleep(max(0.5, args.interval))

    print("\nDone.")


if __name__ == "__main__":
    main()

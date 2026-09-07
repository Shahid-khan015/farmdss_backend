from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api.v1.api import api_router
from app.config import settings
from app.logging_config import configure_logging
from app.routes.auth import router as auth_router
from app.routes.operation_charges import router as operation_charges_router
from app.routes.reports import router as reports_router
from app.routes.sessions import router as sessions_router
from app.routes.wages import router as wages_router
from app.database import Base, engine, describe_url, is_sqlite_fallback_active
from app import models  # noqa: F401
from app.database import SessionLocal
from app.models.iot_reading import IoTReading
from app.models.session import OperationSession
from app.utils.seed_library import seed_library_if_empty

logger = logging.getLogger(__name__)


def _warn_if_multi_worker() -> None:
    """The live pipeline is in-process, so more than one worker breaks it silently.

    Ingestion runs on daemon threads and fans out to WebSockets through an in-memory hub.
    With N workers a client's socket and the thread producing its telemetry can land in
    different processes, and the client simply receives nothing -- no error, no log, just
    a dead gauge. The pollers would also duplicate their Adafruit traffic and multiply
    connections against a capped managed Postgres.

    Warned rather than raised: a hard failure here would brick a deploy over a setting
    that is trivially correctable, and the symptom is diagnosable from /health/iot.
    """
    declared = os.environ.get("WEB_CONCURRENCY")
    from_argv = None
    argv = list(sys.argv)
    if "--workers" in argv:
        index = argv.index("--workers")
        if index + 1 < len(argv):
            from_argv = argv[index + 1]

    for source, value in (("WEB_CONCURRENCY", declared), ("--workers", from_argv)):
        try:
            count = int(value) if value is not None else 1
        except ValueError:
            continue
        if count > 1:
            logger.error(
                "%s=%s: the live telemetry pipeline is in-process and REQUIRES a single "
                "worker. With more than one, WebSocket clients will silently receive no "
                "telemetry. Set it to 1 (see render.yaml).",
                source,
                count,
            )


def _start_iot_transports(app: FastAPI) -> None:
    """
    Start the optional IoT transports, and say out loud when they are skipped.

    A silent skip here used to be indistinguishable from a broken feed, a bad key, or a dead
    thread: nothing was logged on the negative path at all.
    """
    stop = threading.Event()
    app.state.iot_transport_stop = stop

    creds_present = bool(settings.AIO_USERNAME) and bool(settings.AIO_KEY)
    if not creds_present:
        logger.warning(
            "Adafruit credentials incomplete (AIO_USERNAME set=%s, AIO_KEY set=%s) - "
            "no IoT transport will start and no telemetry will be ingested.",
            bool(settings.AIO_USERNAME),
            bool(settings.AIO_KEY),
        )

    if not settings.ENABLE_IOT_HTTP_POLLER:
        logger.warning("IoT HTTP poller disabled (ENABLE_IOT_HTTP_POLLER=False)")
    elif not creds_present:
        logger.warning("IoT HTTP poller enabled but not started: Adafruit credentials missing")
    else:
        from app.services.transports.http_poller import run_http_poller_loop

        interval = max(3.0, float(settings.IOT_ACTIVE_POLL_INTERVAL_SEC))
        t = threading.Thread(
            target=run_http_poller_loop,
            args=(stop, interval),
            name="iot-http-poller",
            daemon=True,
        )
        t.start()
        app.state.iot_http_poller_thread = t
        logger.info("IoT HTTP poller started (active interval=%ss)", interval)

    if not settings.ENABLE_IOT_MQTT:
        logger.info("IoT MQTT subscriber disabled (ENABLE_IOT_MQTT=False)")
    elif not creds_present:
        logger.warning("IoT MQTT enabled but not started: Adafruit credentials missing")
    else:
        from app.services.transports.mqtt_subscriber import run_mqtt_subscriber

        t2 = threading.Thread(
            target=run_mqtt_subscriber,
            args=(stop,),
            name="iot-mqtt-subscriber",
            daemon=True,
        )
        t2.start()
        app.state.iot_mqtt_thread = t2
        logger.info("IoT MQTT subscriber thread started")


def _iot_db_snapshot() -> Dict[str, Any]:
    """Database-side view of ingestion health. Never raises - this endpoint must stay reachable."""
    out: Dict[str, Any] = {}
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        out["readings_last_5min"] = int(
            db.scalar(
                select(func.count()).select_from(IoTReading).where(IoTReading.device_timestamp >= cutoff)
            )
            or 0
        )
        out["readings_total"] = int(db.scalar(select(func.count()).select_from(IoTReading)) or 0)

        rows = db.execute(
            select(IoTReading.feed_key, func.max(IoTReading.device_timestamp)).group_by(
                IoTReading.feed_key
            )
        ).all()
        out["newest_per_feed"] = {
            feed_key: (ts.isoformat() if ts is not None else None) for feed_key, ts in rows
        }

        active = db.scalars(
            select(OperationSession)
            .where(OperationSession.status.in_(("active", "paused")))
            .order_by(OperationSession.started_at.desc())
            .limit(1)
        ).first()
        out["active_session"] = (
            None
            if active is None
            else {
                "id": str(active.id),
                "status": active.status,
                "gps_tracking_enabled": bool(active.gps_tracking_enabled),
                "started_at": active.started_at.isoformat() if active.started_at else None,
            }
        )
    except Exception as exc:
        out["error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        db.close()
    return out


def create_app() -> FastAPI:
    # Must run before anything else can emit: uvicorn configures only its own loggers, so without
    # this every app-level INFO/DEBUG is dropped by logging.lastResort.
    configure_logging(settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.SEED_LIBRARY_ON_STARTUP:
            db = SessionLocal()
            try:
                seed_library_if_empty(db)
            except Exception:
                logger.exception("Library seeding failed; continuing startup")
            finally:
                db.close()

        if is_sqlite_fallback_active():
            logger.error(
                "Running on the SQLite fallback database - data written here is not persisted to "
                "the configured Postgres and will not survive a restart."
            )

        # Order matters: the hub captures the running event loop, and the ingestion
        # threads publish into it, so it must exist before they start. The gate is
        # resolved next so a transport that starts dormant knows it.
        from app.services.ingest_buffer import BUFFER
        from app.services.live_hub import HUB
        from app.services.session_gate import GATE

        _warn_if_multi_worker()
        await HUB.start()
        GATE.refresh()

        _start_iot_transports(app)
        try:
            yield
        finally:
            stop = getattr(app.state, "iot_transport_stop", None)
            if stop is not None:
                stop.set()
            # Unblock any transport parked in wait_for_change so it can exit promptly.
            GATE.wake()
            try:
                # A redeploy must not strand buffered readings.
                BUFFER.flush()
            except Exception:
                logger.exception("final buffer flush on shutdown failed")
            await HUB.stop()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
    )

    # Allow Expo/dev clients to call API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)
    # Auth routes (same /api/v1 prefix as REST API clients expect).
    app.include_router(auth_router, prefix=settings.API_V1_PREFIX)
    app.include_router(sessions_router)
    # Wages replaced by operation charges (Prompt 4)
    # app.include_router(wages_router)
    app.include_router(reports_router)
    app.include_router(operation_charges_router)

    # Auto-create tables only for SQLite fallback (dev convenience).
    if str(engine.url).startswith("sqlite"):
        Base.metadata.create_all(bind=engine)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/health/iot")
    def health_iot():
        """
        One URL that answers "why is there no telemetry?" - credentials, transport threads,
        the last poll cycle, and what the database actually holds. Exposes no secrets.
        """
        from app.services.ingest_buffer import BUFFER
        from app.services.live_hub import HUB
        from app.services.session_gate import GATE
        from app.services.transports.http_poller import STATUS

        poller_thread = getattr(app.state, "iot_http_poller_thread", None)
        mqtt_thread = getattr(app.state, "iot_mqtt_thread", None)

        return {
            "database": dict(
                describe_url(str(engine.url)),
                sqlite_fallback_active=is_sqlite_fallback_active(),
            ),
            "credentials": {
                "aio_username_set": bool(settings.AIO_USERNAME),
                "aio_key_set": bool(settings.AIO_KEY),
            },
            "config": {
                "http_poller_enabled": settings.ENABLE_IOT_HTTP_POLLER,
                "mqtt_enabled": settings.ENABLE_IOT_MQTT,
                "active_poll_interval_sec": settings.IOT_ACTIVE_POLL_INTERVAL_SEC,
                "idle_poll_interval_sec": settings.IOT_IDLE_POLL_INTERVAL_SEC,
                "poll_limit": settings.IOT_HTTP_POLL_LIMIT,
                "default_device_id": settings.IOT_DEFAULT_DEVICE_ID,
                "fetch_through_enabled": settings.IOT_FETCH_THROUGH_ENABLED,
                "stale_after_sec": settings.IOT_STALE_AFTER_SEC,
            },
            "threads": {
                "http_poller_alive": bool(poller_thread is not None and poller_thread.is_alive()),
                "mqtt_alive": bool(mqtt_thread is not None and mqtt_thread.is_alive()),
            },
            "poller": STATUS.snapshot(),
            # Session-gated live pipeline: gate open/closed decides dormancy, the buffer
            # is the micro-batch, the hub is the WebSocket fan-out.
            "gate": GATE.snapshot(),
            "buffer": BUFFER.snapshot(),
            "live": HUB.snapshot(),
            "data": _iot_db_snapshot(),
        }

    return app


app = create_app()

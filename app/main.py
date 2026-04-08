from __future__ import annotations

import logging
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.config import settings
from app.routes.auth import router as auth_router
from app.routes.operation_charges import router as operation_charges_router
from app.routes.reports import router as reports_router
from app.routes.sessions import router as sessions_router
from app.routes.wages import router as wages_router
from app.database import Base, engine
from app import models  # noqa: F401
from app.database import SessionLocal
from app.utils.seed_library import seed_library_if_empty

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        debug=settings.DEBUG,
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

    @app.on_event("startup")
    def _seed_library_data():
        db = SessionLocal()
        try:
            seed_library_if_empty(db)
        finally:
            db.close()

    @app.on_event("startup")
    def _start_iot_transports():
        """Optional background IoT transports (HTTP poll / MQTT); see settings flags."""
        stop = threading.Event()
        app.state.iot_transport_stop = stop

        if settings.ENABLE_IOT_HTTP_POLLER and settings.AIO_USERNAME and settings.AIO_KEY:
            from app.services.transports.http_poller import run_http_poller_loop

            interval = max(5.0, float(settings.IOT_HTTP_POLL_INTERVAL_SEC))
            t = threading.Thread(
                target=run_http_poller_loop,
                args=(stop, interval),
                name="iot-http-poller",
                daemon=True,
            )
            t.start()
            app.state.iot_http_poller_thread = t
            logger.info("IoT HTTP poller started (interval=%ss)", interval)

        if settings.ENABLE_IOT_MQTT and settings.AIO_USERNAME and settings.AIO_KEY:
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

    @app.on_event("shutdown")
    def _stop_iot_transports():
        stop = getattr(app.state, "iot_transport_stop", None)
        if stop is not None:
            stop.set()

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()

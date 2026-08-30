from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute path to backend/.env. A CWD-relative ".env" silently resolves to nothing when the
# process is launched from anywhere other than backend/ (e.g. a hosting platform's repo root),
# which drops DATABASE_URL and the Adafruit credentials without any error.
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class Settings(BaseSettings):
    # Ignore unknown keys in .env (e.g. simulator-only vars, tooling) so extra_forbidden never breaks startup.
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    DATABASE_URL: str = "postgresql://user:password@localhost:5432/tractor_dss"
    SECRET_KEY: str = "change-me"
    # If empty, ``app.utils.security`` uses ``SECRET_KEY`` (typical single-secret .env setups).
    JWT_SECRET_KEY: str = ""
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Tractor DSS"
    CORS_ORIGINS: List[str] = [
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:19006",
        "http://127.0.0.1:19006",
    ]

    # Deliberately NOT tied to DEBUG. Falling back to a local SQLite file when a remote Postgres
    # is briefly unreachable boots the app onto an empty, ephemeral database that looks healthy.
    # Opt in explicitly for offline dev only.
    ALLOW_SQLITE_FALLBACK: bool = False
    DB_CONNECT_TIMEOUT_SEC: int = 15
    SEED_LIBRARY_ON_STARTUP: bool = True

    # Adafruit IO (HTTP + MQTT)
    AIO_USERNAME: str = ""
    AIO_KEY: str = ""
    IOT_DEFAULT_DEVICE_ID: str = "default"
    # Legacy name, superseded by IOT_ACTIVE_POLL_INTERVAL_SEC below. Retained so existing .env
    # files keep parsing; the poller no longer reads it.
    IOT_HTTP_POLL_INTERVAL_SEC: float = 7.0
    IOT_HTTP_POLL_LIMIT: int = 5
    ENABLE_IOT_HTTP_POLLER: bool = False
    ENABLE_IOT_MQTT: bool = False
    IOT_MQTT_BROKER: str = "io.adafruit.com"
    IOT_MQTT_PORT: int = 1883

    # Poll cadence is gated on whether any session is running: a permanently hot poll keeps a
    # serverless Postgres compute awake and burns CPU for data nobody is reading. Set the two
    # intervals equal to disable the gating.
    IOT_ACTIVE_POLL_INTERVAL_SEC: float = 5.0
    IOT_IDLE_POLL_INTERVAL_SEC: float = 120.0
    # Hard wall-clock budget for one poll cycle's HTTP phase, so a slow feed cannot stall the loop.
    IOT_POLL_CYCLE_BUDGET_SEC: float = 20.0

    # Fetch-through: when /iot/latest finds stale data it pulls from Adafruit inline before
    # answering, so the first dashboard poll after a cold start returns live values.
    IOT_FETCH_THROUGH_ENABLED: bool = True
    IOT_STALE_AFTER_SEC: float = 20.0
    IOT_FETCH_THROUGH_COOLDOWN_SEC: float = 5.0
    IOT_FETCH_THROUGH_TIMEOUT_SEC: float = 8.0


settings = Settings()

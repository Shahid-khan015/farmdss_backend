from __future__ import annotations

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ignore unknown keys in .env (e.g. simulator-only vars, tooling) so extra_forbidden never breaks startup.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql://user:password@localhost:5432/tractor_dss"
    SECRET_KEY: str = "change-me"
    # If empty, ``app.utils.security`` uses ``SECRET_KEY`` (typical single-secret .env setups).
    JWT_SECRET_KEY: str = ""
    DEBUG: bool = True

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Tractor DSS"
    CORS_ORIGINS: List[str] = [
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://localhost:19006",
        "http://127.0.0.1:19006",
    ]

    # Adafruit IO (HTTP + MQTT)
    AIO_USERNAME: str = ""
    AIO_KEY: str = ""
    IOT_DEFAULT_DEVICE_ID: str = "default"
    IOT_HTTP_POLL_INTERVAL_SEC: float = 7.0
    IOT_HTTP_POLL_LIMIT: int = 5
    ENABLE_IOT_HTTP_POLLER: bool = False
    ENABLE_IOT_MQTT: bool = False
    IOT_MQTT_BROKER: str = "io.adafruit.com"
    IOT_MQTT_PORT: int = 1883


settings = Settings()

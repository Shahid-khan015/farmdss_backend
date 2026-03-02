from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql://user:password@localhost:5432/tractor_dss"
    SECRET_KEY: str = "change-me"
    DEBUG: bool = True

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Tractor DSS"


settings = Settings()


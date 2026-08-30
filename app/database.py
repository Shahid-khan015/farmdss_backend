from __future__ import annotations

import logging
import time
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Set when the SQLite fallback engages, so /health/iot can report a phantom database instead of
# letting it masquerade as a healthy deployment.
SQLITE_FALLBACK_ACTIVE = False


class Base(DeclarativeBase):
    pass


def normalize_database_url(raw_url: str) -> str:
    """
    Make a hosted-provider DATABASE_URL usable by SQLAlchemy 2.x.

    - ``postgres://`` is what Render/Heroku emit but SQLAlchemy 2 has no such dialect; it raises
      NoSuchModuleError at import, which no OperationalError handler can catch.
    - Managed Postgres (Neon, Supabase, Render) requires TLS, and psycopg2 defaults to
      ``sslmode=prefer``; make it explicit for any non-local host.
    """
    if not raw_url:
        return raw_url

    url = make_url(raw_url)

    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")

    if url.get_backend_name() == "postgresql":
        host = (url.host or "").lower()
        if host not in _LOCAL_HOSTS and "sslmode" not in url.query:
            url = url.update_query_dict({"sslmode": "require"})

    return url.render_as_string(hide_password=False)


def describe_url(raw_url: str) -> Dict[str, Any]:
    """Connection summary safe to expose over HTTP — never includes the password."""
    try:
        url: URL = make_url(raw_url)
    except Exception:  # pragma: no cover - defensive
        return {"dialect": "unknown", "host": None, "database": None}
    return {
        "dialect": url.get_backend_name(),
        "driver": url.drivername,
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "username": url.username,
    }


def is_sqlite_fallback_active() -> bool:
    """True when the app booted onto the local SQLite file instead of the configured database."""
    return SQLITE_FALLBACK_ACTIVE


def _sqlite_engine(url: str):
    return create_engine(url, connect_args={"check_same_thread": False})


def _create_engine_with_fallback():
    global SQLITE_FALLBACK_ACTIVE

    url = normalize_database_url(settings.DATABASE_URL)

    if url.startswith("sqlite"):
        return _sqlite_engine(url)

    connect_args: dict = {}
    if "postgresql" in url:
        # A managed/serverless Postgres that has auto-suspended needs time to wake; a short
        # timeout makes a cold start look identical to an outage.
        connect_args["connect_timeout"] = int(settings.DB_CONNECT_TIMEOUT_SEC)

    eng = create_engine(
        url,
        pool_pre_ping=True,
        # Modest by design: managed Postgres plans cap connections, and the ingestion thread plus
        # any fetch-through both borrow a connection alongside the request that triggered them.
        pool_size=5,
        max_overflow=5,
        pool_timeout=30,
        pool_recycle=300,
        connect_args=connect_args,
    )

    # Smoke-test with a short retry: a suspended compute typically answers on the second attempt.
    last_exc = None
    for attempt in range(3):
        try:
            with eng.connect() as _:
                pass
            return eng
        except OperationalError as exc:
            last_exc = exc
            logger.warning(
                "Database connect attempt %s/3 failed (%s): %s",
                attempt + 1,
                describe_url(url).get("host"),
                exc,
            )
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))

    if settings.ALLOW_SQLITE_FALLBACK:
        logger.error(
            "DATABASE UNREACHABLE - falling back to local SQLite because ALLOW_SQLITE_FALLBACK=True. "
            "This database is empty and, on a hosted service, ephemeral. Nothing persisted here "
            "survives a restart. Configured host was %s.",
            describe_url(url).get("host"),
        )
        SQLITE_FALLBACK_ACTIVE = True
        return _sqlite_engine("sqlite:///./tractor_dss.db")

    logger.error(
        "Database unreachable at host=%s and ALLOW_SQLITE_FALLBACK is False; refusing to start on a "
        "phantom database.",
        describe_url(url).get("host"),
    )
    raise last_exc  # type: ignore[misc]


engine = _create_engine_with_fallback()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

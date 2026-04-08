from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _create_engine_with_fallback():
    url = settings.DATABASE_URL

    # SQLite dev fallback if Postgres/docker isn't available (keeps app runnable end-to-end).
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})

    connect_args: dict = {}
    if "postgresql" in url:
        # Avoid hanging for minutes when Postgres is down or unreachable (common dev pain).
        connect_args["connect_timeout"] = 5

    try:
        eng = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            connect_args=connect_args,
        )
        # smoke-test connection early
        with eng.connect() as _:
            pass
        return eng
    except OperationalError:
        if settings.DEBUG:
            sqlite_url = "sqlite:///./tractor_dss.db"
            return create_engine(sqlite_url, connect_args={"check_same_thread": False})
        raise


engine = _create_engine_with_fallback()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


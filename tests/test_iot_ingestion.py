"""
Regression cover for the deployment-hardening changes to the IoT pipeline.

These pin the behaviours that differ between a developer machine and a hosted service: URL
handling for managed Postgres, refusing the SQLite fallback in production, batch dedup semantics,
and the session-gated poll cadence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import database
from app.database import Base, normalize_database_url
from app.models.iot_reading import IoTReading
from app.models.session import OperationSession
from app.services.ingestion_pipeline import ingest_normalized_batch, ingest_reading
from app.services.normalizer import NormalizedReading
from app.services.transports.http_poller import choose_interval


# --- DATABASE_URL normalization -------------------------------------------------------------


def test_render_style_postgres_scheme_is_rewritten_for_sqlalchemy_2():
    # SQLAlchemy 2 has no "postgres" dialect; left alone this raises NoSuchModuleError at import,
    # which no OperationalError handler can catch.
    out = normalize_database_url("postgres://u:p@ep-cool.neon.tech/neondb")
    assert out.startswith("postgresql+psycopg2://")


def test_managed_host_gets_sslmode_require():
    out = normalize_database_url("postgresql://u:p@db.supabase.co:5432/postgres")
    assert "sslmode=require" in out


def test_explicit_sslmode_is_not_overridden():
    out = normalize_database_url("postgresql://u:p@db.supabase.co:5432/postgres?sslmode=verify-full")
    assert "sslmode=verify-full" in out
    assert "sslmode=require" not in out


def test_local_host_is_left_without_ssl_and_password_encoding_survives():
    out = normalize_database_url("postgresql://postgres:P%40ssw0rd@localhost:5432/tractor_dss")
    assert "sslmode" not in out
    assert "P%40ssw0rd" in out


def test_sqlite_url_passes_through():
    assert normalize_database_url("sqlite:///./x.db") == "sqlite:///./x.db"


# --- SQLite fallback gating ------------------------------------------------------------------


def test_unreachable_database_raises_when_fallback_is_disabled(monkeypatch):
    """
    The old behaviour keyed the fallback off DEBUG, so a momentarily unreachable managed Postgres
    silently booted the app onto an empty, ephemeral SQLite file that reported itself healthy.
    """
    monkeypatch.setattr(database.settings, "DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nope")
    monkeypatch.setattr(database.settings, "ALLOW_SQLITE_FALLBACK", False)
    monkeypatch.setattr(database.settings, "DEBUG", True)  # DEBUG must no longer matter
    monkeypatch.setattr(database.settings, "DB_CONNECT_TIMEOUT_SEC", 1)
    monkeypatch.setattr(database.time, "sleep", lambda *_: None)

    with pytest.raises(OperationalError):
        database._create_engine_with_fallback()


def test_unreachable_database_falls_back_only_when_explicitly_allowed(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nope")
    monkeypatch.setattr(database.settings, "ALLOW_SQLITE_FALLBACK", True)
    monkeypatch.setattr(database.settings, "DB_CONNECT_TIMEOUT_SEC", 1)
    monkeypatch.setattr(database.time, "sleep", lambda *_: None)
    monkeypatch.setattr(database, "SQLITE_FALLBACK_ACTIVE", False)

    eng = database._create_engine_with_fallback()
    assert eng.url.get_backend_name() == "sqlite"
    assert database.is_sqlite_fallback_active() is True


# --- Batch ingestion ------------------------------------------------------------------------


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _reading(feed_key: str, value: str, adafruit_id: str, **kw) -> NormalizedReading:
    return NormalizedReading(
        device_id=kw.pop("device_id", "default"),
        feed_key=feed_key,
        raw_value=value,
        numeric_value=kw.pop("numeric_value", None),
        unit=kw.pop("unit", ""),
        latitude=kw.pop("latitude", None),
        longitude=kw.pop("longitude", None),
        device_timestamp=kw.pop("device_timestamp", datetime.now(timezone.utc)),
        adafruit_id=adafruit_id,
        session_id=kw.pop("session_id", None),
    )


def _make_session(db, status: str = "active", started_at=None) -> OperationSession:
    # SQLite leaves foreign keys unenforced, so the tractor/operator rows are not needed to
    # exercise the attach logic - only the session's own status and flags matter here.
    session = OperationSession(
        id=uuid.uuid4(),
        tractor_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        operation_type="Tillage",
        gps_tracking_enabled=True,
        status=status,
        started_at=started_at or datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    return session


def test_batch_stores_each_reading_once(db):
    batch = [_reading("soil_moisture", str(i), "aio-{}".format(i), numeric_value=float(i)) for i in range(5)]
    assert ingest_normalized_batch(db, batch) == 5
    assert db.scalar(select(func.count()).select_from(IoTReading)) == 5


def test_batch_dedups_against_rows_already_stored(db):
    first = [_reading("soil_moisture", "1", "aio-1"), _reading("soil_moisture", "2", "aio-2")]
    assert ingest_normalized_batch(db, first) == 2

    overlapping = [
        _reading("soil_moisture", "2", "aio-2"),  # already stored
        _reading("soil_moisture", "3", "aio-3"),  # new
    ]
    assert ingest_normalized_batch(db, overlapping) == 1
    assert db.scalar(select(func.count()).select_from(IoTReading)) == 3


def test_duplicate_adafruit_ids_within_one_batch_collapse(db):
    """The unique index would reject these; collapsing in memory keeps the batch insert viable."""
    batch = [_reading("soil_moisture", "1", "aio-dup"), _reading("soil_moisture", "1", "aio-dup")]
    assert ingest_normalized_batch(db, batch) == 1


def test_single_row_entry_point_still_reports_insert_then_duplicate(db):
    inserted, row = ingest_reading(db, _reading("forward_speed", "7", "aio-single"))
    assert inserted is True
    assert row is not None and row.feed_key == "forward_speed"

    inserted_again, row_again = ingest_reading(db, _reading("forward_speed", "7", "aio-single"))
    assert inserted_again is False
    assert row_again is None


def test_readings_attach_to_the_active_session(db):
    session = _make_session(db, status="active")
    ingest_normalized_batch(db, [_reading("soil_moisture", "30", "aio-a")])
    stored = db.scalars(select(IoTReading)).first()
    assert stored.session_id == session.id


def test_readings_still_attach_while_the_session_is_paused(db):
    """
    Previously the attach filter was status == 'active', so everything logged during a pause got a
    NULL session_id - holes in the GPS path and an under-reported worked area at finalize time.
    """
    session = _make_session(db, status="paused")
    ingest_normalized_batch(db, [_reading("position_tracking", '{"lat":1.0,"lon":2.0}', "aio-gps")])
    stored = db.scalars(select(IoTReading)).first()
    assert stored.session_id == session.id


def test_readings_have_no_session_when_none_is_running(db):
    ingest_normalized_batch(db, [_reading("soil_moisture", "30", "aio-orphan")])
    stored = db.scalars(select(IoTReading)).first()
    assert stored.session_id is None


def test_completed_sessions_do_not_capture_readings(db):
    _make_session(db, status="completed")
    ingest_normalized_batch(db, [_reading("soil_moisture", "30", "aio-done")])
    stored = db.scalars(select(IoTReading)).first()
    assert stored.session_id is None


# --- Poll cadence ---------------------------------------------------------------------------


def test_idle_interval_is_used_when_no_session_is_running():
    assert choose_interval(False, 5.0, 120.0) == 120.0


def test_active_interval_is_used_while_a_session_runs():
    assert choose_interval(True, 5.0, 120.0) == 5.0


def test_equal_intervals_disable_the_gating():
    assert choose_interval(False, 5.0, 5.0) == 5.0
    assert choose_interval(True, 5.0, 5.0) == 5.0


def test_idle_interval_never_polls_faster_than_the_active_one():
    assert choose_interval(False, 10.0, 2.0) == 10.0


# --- Concurrent feed fetch --------------------------------------------------------------------


@pytest.fixture()
def adafruit_stub(monkeypatch):
    """Swap the shared client for one backed by an in-process transport."""
    import httpx

    from app.services.transports import http_poller

    state = {"calls": [], "fail_feeds": set()}

    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.rstrip("/").split("/feeds/")[-1].replace("/data", "")
        state["calls"].append(slug)
        if slug in state["fail_feeds"]:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(
            200,
            json=[
                {
                    "id": "{}-point-1".format(slug),
                    "value": "42",
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=http_poller.ADAFRUIT_DATA_BASE
    )
    monkeypatch.setattr(http_poller, "_client", client)
    monkeypatch.setattr(http_poller.settings, "AIO_USERNAME", "tester")
    monkeypatch.setattr(http_poller.settings, "AIO_KEY", "key")
    try:
        yield state
    finally:
        client.close()


def test_fetch_all_feeds_covers_every_configured_feed(adafruit_stub):
    from app.services.normalizer import FEEDS
    from app.services.transports.http_poller import fetch_all_feeds

    batch, failed = fetch_all_feeds(limit=1, budget_sec=10.0)

    assert failed == []
    assert len(batch) == len(FEEDS)
    assert {r.feed_key for r in batch} == set(FEEDS)
    assert len(adafruit_stub["calls"]) == len(FEEDS)


def test_one_broken_feed_does_not_lose_the_others(adafruit_stub):
    """A per-feed failure used to burn three retries with blocking sleeps inside the shared loop."""
    from app.services.normalizer import FEEDS
    from app.services.transports.http_poller import fetch_all_feeds

    adafruit_stub["fail_feeds"] = {"soil-moisture"}
    batch, failed = fetch_all_feeds(limit=1, budget_sec=10.0)

    assert failed == ["soil_moisture"]
    assert len(batch) == len(FEEDS) - 1
    assert "soil_moisture" not in {r.feed_key for r in batch}


def test_fetch_is_skipped_without_credentials(monkeypatch):
    from app.services.normalizer import FEEDS
    from app.services.transports import http_poller

    monkeypatch.setattr(http_poller.settings, "AIO_USERNAME", "")
    monkeypatch.setattr(http_poller.settings, "AIO_KEY", "")

    batch, failed = http_poller.fetch_all_feeds()
    assert batch == []
    assert set(failed) == set(FEEDS)


# --- Staleness detection --------------------------------------------------------------------


def test_stale_when_no_readings_exist(db):
    from app.services import iot_live

    assert iot_live.is_stale(db, "default") is True


def test_fresh_reading_is_not_stale(db):
    from app.services import iot_live

    ingest_normalized_batch(db, [_reading("soil_moisture", "30", "aio-fresh")])
    assert iot_live.is_stale(db, "default") is False


def test_old_reading_is_stale(db):
    from app.services import iot_live

    old = datetime.now(timezone.utc) - timedelta(hours=2)
    ingest_normalized_batch(
        db, [_reading("soil_moisture", "30", "aio-old", device_timestamp=old)]
    )
    assert iot_live.is_stale(db, "default") is True

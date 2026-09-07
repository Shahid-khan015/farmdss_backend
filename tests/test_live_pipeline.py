"""Tests for the session-gated live telemetry pipeline.

Covers the buffer, the session gate, the thread -> event-loop hub bridge, and the MQTT
envelope fix. The load-bearing one is
`test_stop_flushes_the_buffer_before_the_session_leaves_attachable_status` -- that
ordering is what keeps buffered GPS points in the worked area, and therefore in the bill.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.iot_reading import IoTReading
from app.models.session import OperationSession
from app.services.ingest_buffer import ReadingBuffer
from app.services.live_hub import LiveHub
from app.services.normalizer import NormalizedReading
from app.services.transports.mqtt_subscriber import parse_adafruit_envelope


# --- fixtures -----------------------------------------------------------------


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


def _reading(feed_key: str = "soil_moisture", adafruit_id: str = "a-1", **kw) -> NormalizedReading:
    return NormalizedReading(
        device_id=kw.pop("device_id", "default"),
        feed_key=feed_key,
        raw_value=kw.pop("raw_value", "30"),
        numeric_value=kw.pop("numeric_value", 30.0),
        unit=kw.pop("unit", "%"),
        latitude=kw.pop("latitude", None),
        longitude=kw.pop("longitude", None),
        device_timestamp=kw.pop("device_timestamp", datetime.now(timezone.utc)),
        adafruit_id=adafruit_id,
        session_id=kw.pop("session_id", None),
    )


def _make_session(db, status: str = "active") -> OperationSession:
    session = OperationSession(
        id=uuid.uuid4(),
        tractor_id=uuid.uuid4(),
        operator_id=uuid.uuid4(),
        operation_type="Tillage",
        gps_tracking_enabled=True,
        status=status,
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    return session


# --- MQTT envelope ------------------------------------------------------------


def test_mqtt_envelope_keeps_the_reading_and_the_real_dedup_key():
    """The `/json` topic carries the whole record; the old code wrapped it as the value.

    That made `_parse_numeric` regex the first number out of the JSON -- the record id --
    and the discarded `id` forced a synthetic `mqtt-<uuid4>` key that could never match a
    row the HTTP poller had already stored, double-storing every point.
    """
    from app.services.normalizer import process_iot_data

    raw = '{"id":"0K9XYZ","value":"23.7","feed_id":123456,"created_at":"2026-01-01T00:00:00Z"}'
    record = parse_adafruit_envelope(raw)
    assert record["id"] == "0K9XYZ"
    assert record["value"] == "23.7"

    normalized = process_iot_data("gearbox_temperature", record, default_device_id="default")
    assert normalized is not None
    assert normalized.numeric_value == pytest.approx(23.7)
    assert normalized.adafruit_id == "0K9XYZ"
    assert not normalized.adafruit_id.startswith("mqtt-")


def test_mqtt_and_poller_converge_on_one_row(db):
    """The same data point via both transports must store once, not twice."""
    from app.services.ingestion_pipeline import ingest_normalized_batch
    from app.services.normalizer import process_iot_data

    raw = '{"id":"SAME-ID","value":"41.2","created_at":"2026-01-01T00:00:00Z"}'
    via_mqtt = process_iot_data("soil_moisture", parse_adafruit_envelope(raw), default_device_id="default")
    via_poller = process_iot_data(
        "soil_moisture",
        {"id": "SAME-ID", "value": "41.2", "created_at": "2026-01-01T00:00:00Z"},
        default_device_id="default",
    )
    assert ingest_normalized_batch(db, [via_mqtt]) == 1
    assert ingest_normalized_batch(db, [via_poller]) == 0
    assert db.scalar(select(func.count()).select_from(IoTReading)) == 1


def test_an_empty_feed_is_not_reported_as_a_failed_feed(monkeypatch):
    """A feed that exists but has never been written to must not look like an outage.

    Found against the live account: `machine-status` exists and is mapped, but holds no
    data points, so `/data` returns `[]`. Treating that as a failure put it in
    `last_cycle_feeds_failed` on every single cycle -- a permanent false alarm that
    teaches people to ignore the one field that would show a real outage.
    """
    import httpx

    from app.services.transports import http_poller

    def handler(request: httpx.Request) -> httpx.Response:
        if "machine-status" in request.url.path:
            return httpx.Response(200, json=[])  # exists, but never written to
        if "soil-moisture" in request.url.path:
            return httpx.Response(500, json={"error": "boom"})  # a genuine failure
        return httpx.Response(
            200,
            json=[{"id": "x-1", "value": "1", "created_at": "2026-01-01T00:00:00Z"}],
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=http_poller.ADAFRUIT_DATA_BASE
    )
    monkeypatch.setattr(http_poller, "_client", client)
    monkeypatch.setattr(http_poller.settings, "AIO_USERNAME", "tester")
    monkeypatch.setattr(http_poller.settings, "AIO_KEY", "key")

    try:
        _batch, failed = http_poller.fetch_all_feeds(limit=1, budget_sec=10.0)
    finally:
        client.close()

    assert failed == ["soil_moisture"], "only the HTTP error is a failure"
    assert http_poller.STATUS.last_cycle_feeds_empty == ["machine_status"]


def test_mqtt_envelope_falls_back_for_a_bare_value():
    """The plain (non-`/json`) topic delivers just the value; that must still work."""
    assert parse_adafruit_envelope("23.7") == {"value": "23.7", "id": None}
    assert parse_adafruit_envelope("not json at all") == {"value": "not json at all", "id": None}


# --- buffer -------------------------------------------------------------------


def test_buffer_is_due_on_item_count():
    buf = ReadingBuffer(max_items=3, max_age_sec=999.0)
    assert buf.is_due() is False
    for i in range(2):
        buf.add(_reading(adafruit_id=f"a-{i}"))
    assert buf.is_due() is False
    buf.add(_reading(adafruit_id="a-2"))
    assert buf.is_due() is True


def test_buffer_is_due_on_age():
    buf = ReadingBuffer(max_items=999, max_age_sec=0.5)
    buf.add(_reading())
    assert buf.is_due() is False
    time.sleep(0.6)
    assert buf.is_due() is True


def test_buffer_flush_drains_even_when_the_write_fails(monkeypatch):
    """A failed flush must not re-queue: the poller backfills from Adafruit anyway."""
    buf = ReadingBuffer()
    buf.add(_reading())

    def _boom(*_args, **_kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr("app.services.ingest_buffer.SessionLocal", _boom)
    assert buf.flush() == 0
    assert buf.pending() == 0
    assert buf.dropped_total == 1


def test_buffer_add_is_not_blocked_by_an_in_flight_write():
    """Two locks on purpose: producers must never wait behind a database round trip."""
    buf = ReadingBuffer()
    buf._write_lock.acquire()
    try:
        done = threading.Event()

        def _producer():
            buf.add(_reading(adafruit_id="concurrent"))
            done.set()

        threading.Thread(target=_producer, daemon=True).start()
        assert done.wait(timeout=1.0), "add() blocked while a write was in progress"
        assert buf.pending() == 1
    finally:
        buf._write_lock.release()


# --- hub: the thread -> loop bridge -------------------------------------------


class _FakeSocket:
    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, payload) -> None:
        self.sent.append(payload)


def test_hub_delivers_a_frame_published_from_a_non_loop_thread():
    """The whole point of the hub: ingestion runs on daemon threads, sockets on the loop."""

    async def scenario():
        hub = LiveHub()
        await hub.start()
        socket = _FakeSocket()
        await hub.register("session-1", socket)

        # Publish from a real worker thread, as the MQTT subscriber does.
        t = threading.Thread(
            target=hub.publish_threadsafe, args=("session-1", {"type": "reading", "v": 1})
        )
        t.start()
        t.join()

        for _ in range(50):
            if socket.sent:
                break
            await asyncio.sleep(0.01)
        await hub.stop()
        return socket.sent

    sent = asyncio.run(scenario())
    assert sent == [{"type": "reading", "v": 1}]


def test_hub_publish_is_a_noop_before_start_and_after_stop():
    """Must never raise: a display frame is not worth failing an ingest over."""
    hub = LiveHub()
    hub.publish_threadsafe("s", {"a": 1})  # never started
    assert hub.published == 0

    async def scenario():
        await hub.start()
        await hub.stop()

    asyncio.run(scenario())
    hub.publish_threadsafe("s", {"a": 1})  # already stopped
    assert hub.published == 0


def test_hub_drops_rather_than_blocking_when_the_queue_is_full():
    """Backpressure must degrade the display, never stall an ingestion thread."""

    async def scenario():
        hub = LiveHub()
        # Wire the loop and queue by hand, without the drain task, so the queue stays
        # full for the duration of the test.
        hub._loop = asyncio.get_running_loop()
        hub._queue = asyncio.Queue(maxsize=2)
        hub._queue.put_nowait(("s", {}))
        hub._queue.put_nowait(("s", {}))

        hub.publish_threadsafe("s", {"overflow": True})
        await asyncio.sleep(0)  # let call_soon_threadsafe run
        return hub.dropped, hub.published

    dropped, published = asyncio.run(scenario())
    assert dropped == 1
    assert published == 0


def test_hub_reports_only_sessions_with_listeners():
    async def scenario():
        hub = LiveHub()
        await hub.start()
        socket = _FakeSocket()
        await hub.register("watched", socket)
        watched = hub.watched_sessions()
        await hub.unregister("watched", socket)
        after = hub.watched_sessions()
        await hub.stop()
        return watched, after

    watched, after = asyncio.run(scenario())
    assert watched == ["watched"]
    assert after == []


# --- session gate -------------------------------------------------------------


def test_gate_opens_and_closes_with_session_status(monkeypatch):
    """The gate follows session status, and closing it disposes the pool.

    Uses its own sessionmaker rather than the shared `db` fixture: `refresh()` closes the
    session it opens, and `Session.close()` detaches instances -- so handing it the test's
    own session would make later mutations silent no-ops.
    """
    from app.services import session_gate

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr(session_gate, "SessionLocal", Factory)
    disposed = []
    monkeypatch.setattr(session_gate, "release_pool", lambda: disposed.append(True))

    gate = session_gate.SessionGate()
    assert gate.refresh() is False
    assert gate.is_open() is False

    work = Factory()
    try:
        session = _make_session(work, status="active")
        assert gate.refresh() is True
        assert gate.is_open() is True

        # Paused keeps the gate OPEN -- telemetry must keep attaching for the GPS trail.
        session.status = "paused"
        work.commit()
        assert gate.refresh() is True
        assert disposed == [], "a pause must not dispose the pool; the session is still live"

        session.status = "completed"
        work.commit()
        assert gate.refresh() is False
        assert disposed == [True], "closing the gate must dispose the pool so the DB can sleep"
    finally:
        work.close()
        engine.dispose()


def test_gate_fails_open_when_the_database_is_unreachable(monkeypatch):
    """A transient DB error must not silently stop ingestion during a live operation."""
    from app.services import session_gate

    def _boom():
        raise RuntimeError("database down")

    monkeypatch.setattr(session_gate, "SessionLocal", _boom)
    gate = session_gate.SessionGate()
    assert gate.refresh() is True


def test_gate_wait_for_change_unblocks_on_wake():
    from app.services.session_gate import SessionGate

    gate = SessionGate()
    result = {}

    def _waiter():
        result["flipped"] = gate.wait_for_change(timeout=5.0)

    t = threading.Thread(target=_waiter, daemon=True)
    t.start()
    time.sleep(0.05)
    gate.wake()
    t.join(timeout=2.0)
    assert result.get("flipped") is True


# --- the ordering that protects the bill --------------------------------------


def test_stop_flushes_the_buffer_before_the_session_leaves_attachable_status(db):
    """Buffered GPS points must land on the session, not on NULL.

    `resolve_target_session` only matches `active`/`paused`. If the buffer were flushed
    after `status = "completed"`, those readings would attach to nothing and disappear
    from the GPS path, the worked area, and the invoice. This reproduces the correct
    order and asserts attribution.
    """
    from app.services.ingestion_pipeline import ingest_normalized_batch

    session = _make_session(db, status="active")
    buffered = [
        _reading(
            feed_key="position_tracking",
            adafruit_id=f"gps-{i}",
            raw_value='{"lat":12.9,"lon":77.6}',
            numeric_value=None,
        )
        for i in range(3)
    ]

    # 1. Flush while still attachable (what stop_session now does first).
    assert ingest_normalized_batch(db, buffered) == 3
    # 2. Only then close the session.
    session.status = "completed"
    db.commit()

    rows = list(db.scalars(select(IoTReading)))
    assert len(rows) == 3
    assert all(r.session_id == session.id for r in rows), (
        "buffered readings must be attributed to the session that was running"
    )


def test_flushing_after_completion_would_lose_the_attribution(db):
    """The inverse, pinned deliberately: this is the bug the ordering prevents."""
    from app.services.ingestion_pipeline import ingest_normalized_batch

    session = _make_session(db, status="active")
    session.status = "completed"
    db.commit()

    assert ingest_normalized_batch(db, [_reading(feed_key="position_tracking", adafruit_id="late")]) == 1
    stored = db.scalars(select(IoTReading)).first()
    assert stored.session_id is None, (
        "a completed session is not attachable -- which is exactly why stop_session "
        "must flush before it changes the status"
    )

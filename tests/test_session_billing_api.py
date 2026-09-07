"""End-to-end cover for the session lifecycle that produces a charge.

Drives the real routes -- start, pause, resume, stop, cancel, area-summary, session
summary, export -- because the bugs these pin were bugs in the *wiring*, not in the
arithmetic: the charge was recomputed on read paths, so it could differ between the stop
response, the area-summary poll and the summary screen for one and the same session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.database import Base
from app.main import app
from app.models.enums import DriveMode, ImplementType
from app.models.implement import Implement
from app.models.iot_reading import IoTReading
from app.models.operation_charge import OperationCharge
from app.models.session import OperationSession
from app.models.tractor import Tractor
from app.models.user import User
from app.utils.security import create_access_token


@pytest.fixture()
def api(monkeypatch):
    """A client wired to an isolated in-memory database.

    StaticPool so background tasks and the request handler share one connection, and the
    session gate is stubbed out -- it opens real database sessions of its own and would
    otherwise reach for the app's configured engine.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = Factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    from app.services import session_gate

    monkeypatch.setattr(session_gate.GATE, "refresh", lambda: True)

    with TestClient(app) as client:
        yield client, Factory

    app.dependency_overrides.pop(get_db, None)
    engine.dispose()


# --- fixture builders ---------------------------------------------------------


def _user(db, role: str, name: str) -> User:
    user = User(
        id=uuid.uuid4(),
        phone_number="9{}".format(uuid.uuid4().int % 10**9),
        password_hash="x",
        name=name,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    # Detach with its attributes already loaded: the caller uses it after this session is
    # closed, and a still-attached instance would try to refresh against a dead session.
    db.refresh(user)
    db.expunge(user)
    return user


def _token(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(user.id), 'role': user.role})}"}


def setup_world(Factory, *, per_ha=None, per_hour=None, operation="Tillage", width_m=2.5):
    """An owner with a rate card, their tractor + implement, an operator and a farmer."""
    db = Factory()
    try:
        owner = _user(db, "owner", "Owner")
        operator = _user(db, "operator", "Operator")
        farmer = _user(db, "farmer", "Farmer")

        tractor = Tractor(
            id=uuid.uuid4(),
            name="T1",
            model="M1",
            drive_mode=DriveMode.WD2,
            owner_id=owner.id,
        )
        db.add(tractor)
        implement = Implement(
            id=uuid.uuid4(),
            name="I1",
            implement_type=ImplementType.CULTIVATOR,
            working_width_m=width_m,
            owner_id=owner.id,
        )
        db.add(implement)

        if per_ha is not None or per_hour is not None:
            db.add(
                OperationCharge(
                    id=uuid.uuid4(),
                    owner_id=owner.id,
                    operation_type=operation,
                    charge_per_ha=Decimal(str(per_ha)) if per_ha is not None else Decimal("0"),
                    charge_per_hour=Decimal(str(per_hour)) if per_hour is not None else None,
                    currency="INR",
                )
            )
        db.commit()
        return {
            "owner": owner,
            "operator": operator,
            "farmer": farmer,
            "tractor_id": str(tractor.id),
            "implement_id": str(implement.id),
        }
    finally:
        db.close()


def start(client, world, operation="Tillage", *, with_implement=True):
    payload = {
        "tractor_id": world["tractor_id"],
        "operation_type": operation,
        "client_farmer_id": str(world["farmer"].id),
        "gps_tracking_enabled": True,
    }
    if with_implement:
        payload["implement_id"] = world["implement_id"]
    return client.post("/api/v1/sessions/start", json=payload, headers=_token(world["operator"]))


def add_gps(Factory, session_id, lat, lon, when):
    db = Factory()
    try:
        db.add(
            IoTReading(
                id=uuid.uuid4(),
                device_id="default",
                feed_key="position_tracking",
                raw_value=f'{{"lat": {lat}, "lon": {lon}}}',
                unit="",
                latitude=lat,
                longitude=lon,
                device_timestamp=when,
                adafruit_id=f"gps-{uuid.uuid4()}",
                session_id=uuid.UUID(session_id),
            )
        )
        db.commit()
    finally:
        db.close()


def backdate_start(Factory, session_id, *, hours):
    """Move started_at into the past so a stop yields a known billable duration."""
    db = Factory()
    try:
        row = db.get(OperationSession, uuid.UUID(session_id))
        row.started_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        db.commit()
        return row.started_at
    finally:
        db.close()


# --- start: the rate is locked, or the session is refused ---------------------


def test_start_locks_the_rate_and_unit_onto_the_session(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)

    response = start(client, world)

    assert response.status_code == 201
    body = response.json()
    assert body["charge_unit"] == "per_ha"
    assert body["charge_per_ha_applied"] == pytest.approx(1200.0)
    assert body["rate_currency"] == "INR"
    assert body["cost_finalized_at"] is None


def test_start_locks_the_hourly_rate_for_threshing(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=0, per_hour=450, operation="Threshing")

    body = start(client, world, "Threshing").json()

    assert body["charge_unit"] == "per_hour"
    assert body["charge_per_ha_applied"] == pytest.approx(450.0)


def test_start_is_refused_when_the_owner_has_no_rate_for_the_operation(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200, operation="Tillage")

    response = start(client, world, "Harvesting")

    assert response.status_code == 422
    assert "Harvesting" in response.json()["detail"]

    db = Factory()
    try:
        assert db.query(OperationSession).count() == 0  # nothing half-created
    finally:
        db.close()


def test_start_is_refused_when_the_tractor_has_no_owner(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    db = Factory()
    try:
        db.get(Tractor, uuid.UUID(world["tractor_id"])).owner_id = None
        db.commit()
    finally:
        db.close()

    response = start(client, world)

    assert response.status_code == 422
    assert "owner" in response.json()["detail"].lower()


# --- stop: the charge is computed once from measured work ---------------------


def test_stop_bills_measured_area_at_the_locked_rate(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1000, width_m=2.0)
    session_id = start(client, world).json()["id"]
    started = backdate_start(Factory, session_id, hours=1)

    add_gps(Factory, session_id, 20.0000, 75.0000, started + timedelta(minutes=1))
    add_gps(Factory, session_id, 20.0010, 75.0000, started + timedelta(minutes=2))

    stopped = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=_token(world["operator"])).json()

    assert stopped["status"] == "completed"
    assert stopped["cost_finalized_at"] is not None
    area = stopped["area_ha"]
    assert area > 0
    assert stopped["total_cost_inr"] == pytest.approx(
        float((Decimal("1000") * Decimal(str(area))).quantize(Decimal("0.01"))), abs=0.01
    )


def test_stop_bills_threshing_on_worked_hours_not_wall_clock(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=0, per_hour=600, operation="Threshing")
    session_id = start(client, world, "Threshing").json()["id"]
    backdate_start(Factory, session_id, hours=4)

    headers = _token(world["operator"])
    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    # Rewrite the pause interval to a known hour rather than sleeping.
    db = Factory()
    try:
        from app.models.session import SessionPause

        pause = db.query(SessionPause).one()
        pause.paused_at = datetime.now(timezone.utc) - timedelta(hours=3)
        db.commit()
    finally:
        db.close()
    client.patch(f"/api/v1/sessions/{session_id}/resume", headers=headers)
    db = Factory()
    try:
        from app.models.session import SessionPause

        pause = db.query(SessionPause).one()
        pause.resumed_at = pause.paused_at + timedelta(hours=1)
        db.commit()
    finally:
        db.close()

    stopped = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers).json()

    assert stopped["billable_hours"] == pytest.approx(3.0, abs=0.01)  # 4h - 1h paused
    assert stopped["total_cost_inr"] == pytest.approx(1800.0, abs=1.0)  # 600 x 3


def test_gps_recorded_while_paused_is_not_billed_but_is_still_on_the_map(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1000, width_m=2.0)
    session_id = start(client, world).json()["id"]
    started = backdate_start(Factory, session_id, hours=2)
    headers = _token(world["operator"])

    add_gps(Factory, session_id, 20.0000, 75.0000, started + timedelta(minutes=1))
    add_gps(Factory, session_id, 20.0010, 75.0000, started + timedelta(minutes=2))

    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    db = Factory()
    try:
        from app.models.session import SessionPause

        pause = db.query(SessionPause).one()
        pause.paused_at = started + timedelta(minutes=10)
        db.commit()
    finally:
        db.close()
    # Transit while paused: legal steps, well inside the GPS noise gate.
    add_gps(Factory, session_id, 20.0030, 75.0000, started + timedelta(minutes=12))
    add_gps(Factory, session_id, 20.0060, 75.0000, started + timedelta(minutes=18))
    client.patch(f"/api/v1/sessions/{session_id}/resume", headers=headers)

    stopped = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers).json()

    path = client.get(f"/api/v1/sessions/{session_id}/gps-path", headers=headers).json()
    assert path["total_points"] == 4  # the trail keeps every fix

    # Billed area is the worked leg only: ~111 m x 2 m.
    assert stopped["area_ha"] == pytest.approx(0.0222, abs=0.002)
    assert stopped["total_cost_inr"] == pytest.approx(22.2, abs=2.0)


def test_a_session_with_no_telemetry_is_billed_zero_and_says_so(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]

    stopped = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=_token(world["operator"])).json()

    assert stopped["area_ha"] == 0.0
    assert stopped["total_cost_inr"] == 0.0
    assert stopped["cost_finalized_at"] is not None
    assert "no measured work recorded" in stopped["cost_note"]


# --- the charge is final ------------------------------------------------------


def test_the_charge_is_identical_across_every_read_path(api):
    """stop, session detail, area-summary, session summary and the CSV export.

    Two of these used to recompute the cost themselves, so the same session could show
    different totals depending on which screen asked.
    """
    client, Factory = api
    world = setup_world(Factory, per_ha=1000, width_m=2.0)
    session_id = start(client, world).json()["id"]
    started = backdate_start(Factory, session_id, hours=1)
    add_gps(Factory, session_id, 20.0000, 75.0000, started + timedelta(minutes=1))
    add_gps(Factory, session_id, 20.0010, 75.0000, started + timedelta(minutes=2))
    headers = _token(world["operator"])

    settled = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers).json()["total_cost_inr"]
    assert settled > 0

    detail = client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()
    summary = client.get(f"/api/v1/reports/session/{session_id}", headers=headers).json()
    area = client.get(f"/api/v1/sessions/{session_id}/area-summary", headers=headers).json()
    export = client.get(f"/api/v1/reports/session/{session_id}/export?format=csv", headers=headers)

    assert detail["total_cost_inr"] == settled
    assert summary["total_cost_inr"] == settled
    assert summary["charge_unit"] == "per_ha"
    assert area["charge_unit"] == "per_ha"
    assert f"{settled:.2f}" in export.text


def test_an_owner_rate_change_after_stop_cannot_re_bill_the_session(api):
    """The regression: the summary endpoint used to overwrite settled totals."""
    client, Factory = api
    world = setup_world(Factory, per_ha=1000, width_m=2.0)
    session_id = start(client, world).json()["id"]
    started = backdate_start(Factory, session_id, hours=1)
    add_gps(Factory, session_id, 20.0000, 75.0000, started + timedelta(minutes=1))
    add_gps(Factory, session_id, 20.0010, 75.0000, started + timedelta(minutes=2))
    headers = _token(world["operator"])

    settled = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers).json()["total_cost_inr"]

    charge_id = client.get("/api/v1/operation-charges", headers=_token(world["owner"])).json()[0]["id"]
    updated = client.patch(
        f"/api/v1/operation-charges/{charge_id}",
        json={"charge_per_ha": 5000},
        headers=_token(world["owner"]),
    )
    assert updated.status_code == 200

    # Every read path, several times over.
    for _ in range(2):
        assert client.get(f"/api/v1/reports/session/{session_id}", headers=headers).json()["total_cost_inr"] == settled
        assert client.get(f"/api/v1/sessions/{session_id}/area-summary", headers=headers).status_code == 200
        assert client.get(f"/api/v1/sessions/{session_id}", headers=headers).json()["total_cost_inr"] == settled


def test_a_rate_change_applies_to_the_next_session_only(api):
    """Locking must not freeze the owner's pricing -- only each session's own copy of it."""
    client, Factory = api
    world = setup_world(Factory, per_ha=1000)
    first = start(client, world).json()
    client.post(f"/api/v1/sessions/{first['id']}/stop", json={}, headers=_token(world["operator"]))

    charge_id = client.get("/api/v1/operation-charges", headers=_token(world["owner"])).json()[0]["id"]
    client.patch(
        f"/api/v1/operation-charges/{charge_id}",
        json={"charge_per_ha": 5000},
        headers=_token(world["owner"]),
    )

    second = start(client, world).json()

    assert first["charge_per_ha_applied"] == pytest.approx(1000.0)
    assert second["charge_per_ha_applied"] == pytest.approx(5000.0)


# --- cancellation -------------------------------------------------------------


def test_cancel_ends_the_session_at_zero(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200, width_m=2.0)
    session_id = start(client, world).json()["id"]
    started = backdate_start(Factory, session_id, hours=1)
    add_gps(Factory, session_id, 20.0000, 75.0000, started + timedelta(minutes=1))
    add_gps(Factory, session_id, 20.0010, 75.0000, started + timedelta(minutes=2))
    headers = _token(world["operator"])

    cancelled = client.post(f"/api/v1/sessions/{session_id}/cancel", headers=headers).json()

    assert cancelled["status"] == "aborted"
    assert cancelled["total_cost_inr"] == 0.0
    assert cancelled["cost_note"] == "Session cancelled - no charge"
    assert cancelled["cost_finalized_at"] is not None
    assert cancelled["area_ha"] > 0  # work is still recorded, just not charged


def test_a_cancelled_session_cannot_then_be_stopped(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]
    headers = _token(world["operator"])

    client.post(f"/api/v1/sessions/{session_id}/cancel", headers=headers)
    again = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers)

    assert again.status_code == 400


def test_cancel_works_from_paused_and_closes_the_pause(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]
    headers = _token(world["operator"])

    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    cancelled = client.post(f"/api/v1/sessions/{session_id}/cancel", headers=headers).json()

    assert cancelled["status"] == "aborted"
    db = Factory()
    try:
        from app.models.session import SessionPause

        assert db.query(SessionPause).filter(SessionPause.resumed_at.is_(None)).count() == 0
    finally:
        db.close()


def test_another_operator_cannot_cancel_someone_elses_session(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]

    db = Factory()
    try:
        intruder = _user(db, "operator", "Someone Else")
    finally:
        db.close()

    response = client.post(f"/api/v1/sessions/{session_id}/cancel", headers=_token(intruder))
    assert response.status_code == 403


# --- pause ledger -------------------------------------------------------------


def test_pause_and_resume_record_a_closed_interval(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]
    headers = _token(world["operator"])

    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    client.patch(f"/api/v1/sessions/{session_id}/resume", headers=headers)
    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    client.patch(f"/api/v1/sessions/{session_id}/resume", headers=headers)

    db = Factory()
    try:
        from app.models.session import SessionPause

        rows = db.query(SessionPause).all()
        assert len(rows) == 2
        assert all(row.resumed_at is not None for row in rows)
    finally:
        db.close()


def test_stopping_while_paused_closes_the_open_interval(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)
    session_id = start(client, world).json()["id"]
    headers = _token(world["operator"])

    client.patch(f"/api/v1/sessions/{session_id}/pause", headers=headers)
    stopped = client.post(f"/api/v1/sessions/{session_id}/stop", json={}, headers=headers)

    assert stopped.status_code == 200
    db = Factory()
    try:
        from app.models.session import SessionPause

        assert db.query(SessionPause).filter(SessionPause.resumed_at.is_(None)).count() == 0
    finally:
        db.close()


# --- rate card scoping --------------------------------------------------------


def test_an_operator_does_not_see_other_owners_rate_cards(api):
    client, Factory = api
    world = setup_world(Factory, per_ha=1200)

    rows = client.get("/api/v1/operation-charges", headers=_token(world["operator"])).json()

    assert rows == []

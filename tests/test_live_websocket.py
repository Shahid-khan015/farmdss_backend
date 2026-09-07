"""End-to-end tests for the live telemetry WebSocket.

These pin the contract the Expo client's silent-reconnect ladder depends on: the
first-message handshake, and the 4401-vs-4403 split that decides whether the client
refreshes its token or gives up.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1.routes import live as live_route
from app.api.v1.routes.live import WS_CLOSE_FORBIDDEN, WS_CLOSE_UNAUTHORIZED
from app.database import Base
from app.main import app
from app.models.session import OperationSession
from app.models.user import User
from app.services.live_hub import HUB
from app.utils.security import create_access_token, create_refresh_token


@pytest.fixture()
def live_db(monkeypatch):
    """An isolated database for the socket's own lookups.

    The handler uses `SessionLocal` directly rather than `Depends(get_db)` -- a WebSocket
    has no request scope -- so it is patched at the module it was imported into.
    """
    # StaticPool: the handler's lookups run in a threadpool thread, and an in-memory
    # SQLite database is per-connection -- without it the socket would see an empty
    # schema rather than the rows this fixture just created.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(live_route, "SessionLocal", Factory)
    try:
        yield Factory
    finally:
        engine.dispose()


def _make_operator(Factory, *, active: bool = True) -> User:
    db = Factory()
    try:
        user = User(
            id=uuid.uuid4(),
            phone_number="9{}".format(uuid.uuid4().int % 10**9),
            password_hash="x",
            name="Operator",
            role="operator",
            is_active=active,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()


def _make_session(Factory, operator_id: uuid.UUID) -> uuid.UUID:
    db = Factory()
    try:
        session = OperationSession(
            id=uuid.uuid4(),
            tractor_id=uuid.uuid4(),
            operator_id=operator_id,
            operation_type="Tillage",
            gps_tracking_enabled=True,
            status="active",
            started_at=datetime.now(timezone.utc),
        )
        db.add(session)
        db.commit()
        return session.id
    finally:
        db.close()


def _token_for(user: User) -> str:
    return create_access_token({"sub": str(user.id), "role": user.role})


def test_handshake_then_live_frame_reaches_the_client(live_db):
    """The whole path: authenticate, register, and receive a frame published from code
    running outside the event loop -- which is where ingestion actually runs."""
    operator = _make_operator(live_db)
    session_id = _make_session(live_db, operator.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": _token_for(operator)})
            ready = ws.receive_json()
            assert ready == {"type": "ready", "session_id": str(session_id)}

            HUB.publish_threadsafe(
                str(session_id), {"type": "reading", "feed_key": "soil_moisture", "numeric_value": 30.0}
            )
            frame = ws.receive_json()
            assert frame["type"] == "reading"
            assert frame["feed_key"] == "soil_moisture"


def test_an_invalid_token_closes_4401_so_the_client_refreshes(live_db):
    """4401 is the retryable code: the client refreshes and reconnects, silently."""
    session_id = _make_session(live_db, uuid.uuid4())

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": "not-a-jwt"})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_UNAUTHORIZED


def test_a_refresh_token_is_rejected_as_4401(live_db):
    """`verify_token` checks signature and exp only; the `type` claim is checked here.

    Without that check a refresh token would authenticate a socket.
    """
    operator = _make_operator(live_db)
    session_id = _make_session(live_db, operator.id)
    refresh = create_refresh_token({"sub": str(operator.id)})

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": refresh})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_UNAUTHORIZED


def test_an_inactive_user_is_rejected_as_4401(live_db):
    operator = _make_operator(live_db, active=False)
    session_id = _make_session(live_db, operator.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": _token_for(operator)})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_UNAUTHORIZED


def test_another_operators_session_closes_4403_not_4401(live_db):
    """4403 is terminal. If this were 4401 the client would refresh and reconnect forever."""
    owner_of_session = _make_operator(live_db)
    intruder = _make_operator(live_db)
    session_id = _make_session(live_db, owner_of_session.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": _token_for(intruder)})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_FORBIDDEN


def test_an_unknown_session_closes_4403(live_db):
    _make_operator(live_db)
    operator = _make_operator(live_db)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{uuid.uuid4()}/live") as ws:
            ws.send_json({"token": _token_for(operator)})
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_json()
            assert exc.value.code == WS_CLOSE_FORBIDDEN


def test_a_client_that_disconnects_is_removed_from_the_hub(live_db):
    """A leaked registration would keep fanning out to a dead socket forever."""
    operator = _make_operator(live_db)
    session_id = _make_session(live_db, operator.id)

    with TestClient(app) as client:
        with client.websocket_connect(f"/api/v1/sessions/{session_id}/live") as ws:
            ws.send_json({"token": _token_for(operator)})
            ws.receive_json()
            assert HUB.connection_count() == 1
        # Give the server's finally block a moment to run.
        for _ in range(50):
            if HUB.connection_count() == 0:
                break
            import time

            time.sleep(0.02)
        assert HUB.connection_count() == 0

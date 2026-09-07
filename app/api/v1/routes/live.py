"""Live telemetry WebSocket for an operation session.

The rest of this backend is synchronous -- `def` routes dispatched into anyio's
threadpool, blocking psycopg2 underneath. This handler is one of only two `async def`s
in the app, so **every** database call here is offloaded with `run_in_threadpool`.
Calling `db.get(...)` directly would block the event loop and stall every other
connected client, not just this one.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.database import SessionLocal
from app.models.session import OperationSession
from app.models.user import User
from app.services.live_hub import HUB
from app.utils.security import verify_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Live"])

# Application close codes. The 4xxx range is reserved for application use, and the split
# between them is what lets the client refresh silently without looping on a real denial.
WS_CLOSE_UNAUTHORIZED = 4401  # token missing/expired/wrong type -> refresh and reconnect
WS_CLOSE_FORBIDDEN = 4403  # authenticated but not allowed here -> stop, tell the user
WS_CLOSE_POLICY = 1008  # malformed handshake
WS_CLOSE_GOING_AWAY = 1012  # server restarting


def _authenticate(token: str) -> Optional[User]:
    """Validate an access token the way `get_current_user` does, minus the HTTP plumbing.

    `verify_token` checks the signature and `exp` only -- it does **not** check the
    `type` claim, so on its own it would happily accept a *refresh* token. Both the
    `type == "access"` check and the `is_active` lookup are replicated here rather than
    skipped; a WebSocket cannot use `Depends(get_current_user)` because `HTTPBearer`
    reads a header the client cannot set.
    """
    payload = verify_token(token)
    if payload is None or payload.get("type") != "access":
        return None

    raw_id = payload.get("sub")
    if raw_id is None:
        return None
    try:
        user_id = uuid.UUID(str(raw_id))
    except ValueError:
        return None

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        # Detach what the caller needs before the Session closes.
        db.expunge(user)
        return user
    finally:
        db.close()


def _token_expiry_epoch(token: str) -> Optional[float]:
    payload = verify_token(token)
    if payload is None:
        return None
    exp = payload.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _may_watch_session(user: User, session_id: uuid.UUID) -> Optional[bool]:
    """None => session not found. True/False => allowed / denied.

    Reuses `_assert_session_access`, the same predicate the HTTP session routes apply, so
    the socket cannot drift from them.
    """
    from app.routes.sessions import _assert_session_access
    from fastapi import HTTPException

    db = SessionLocal()
    try:
        session = db.get(OperationSession, session_id)
        if session is None:
            return None
        try:
            _assert_session_access(session, user, db)
        except HTTPException:
            return False
        return True
    finally:
        db.close()


@router.websocket("/sessions/{session_id}/live")
async def session_live(websocket: WebSocket, session_id: uuid.UUID) -> None:
    """Stream normalized telemetry and alerts for one session.

    Handshake: the client connects, then sends ``{"token": "<access token>"}`` within
    ``WS_AUTH_TIMEOUT_SEC``. Auth is by first message rather than query string because
    React Native cannot set headers on the web target, and a token in a URL ends up in
    access logs.
    """
    await websocket.accept()

    try:
        frame = await asyncio.wait_for(
            websocket.receive_json(), timeout=float(settings.WS_AUTH_TIMEOUT_SEC)
        )
    except asyncio.TimeoutError:
        await websocket.close(code=WS_CLOSE_POLICY, reason="auth timeout")
        return
    except (WebSocketDisconnect, ValueError, TypeError):
        return

    token = (frame or {}).get("token") if isinstance(frame, dict) else None
    if not isinstance(token, str) or not token:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="missing token")
        return

    user = await run_in_threadpool(_authenticate, token)
    if user is None:
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="invalid token")
        return

    allowed = await run_in_threadpool(_may_watch_session, user, session_id)
    if allowed is None:
        await websocket.close(code=WS_CLOSE_FORBIDDEN, reason="session not found")
        return
    if not allowed:
        await websocket.close(code=WS_CLOSE_FORBIDDEN, reason="access denied")
        return

    # Close a little BEFORE the token lapses. A scheduled close lands while the client is
    # healthy and it refreshes silently; waiting for expiry would surface a failure at an
    # arbitrary moment, which is exactly what must not happen mid-pass.
    expires_at = _token_expiry_epoch(token)
    margin = float(settings.WS_TOKEN_EXPIRY_MARGIN_SEC)

    key = str(session_id)
    await HUB.register(key, websocket)
    try:
        await websocket.send_json({"type": "ready", "session_id": key})
        while True:
            if expires_at is not None and time.time() >= (expires_at - margin):
                await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="token expiring")
                return
            try:
                # The read exists to detect disconnects and to service client pings; the
                # timeout is what lets the expiry check above run on a quiet socket.
                await asyncio.wait_for(websocket.receive_text(), timeout=20.0)
            except asyncio.TimeoutError:
                continue
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("live socket error session=%s", key)
    finally:
        await HUB.unregister(key, websocket)

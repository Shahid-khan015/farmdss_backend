from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db
from app.middleware.auth import get_current_user, require_role
from app.models.implement import Implement
from app.models.iot_reading import IoTReading
from app.models.session import (
    FieldObservation,
    IoTAlert,
    OperationSession,
    SessionPause,
    SessionPresetValue,
)
from app.models.tractor import Tractor
from app.models.user import User
from app.schemas.session import (
    AlertListResponse,
    AlertResponse,
    FieldObservationCreate,
    FieldObservationResponse,
    PresetValueResponse,
    SessionDetailResponse,
    SessionResponse,
    SessionStartRequest,
    SessionStopRequest,
)
from app.services.operation_cost_service import RateNotConfigured, lock_session_rate

router = APIRouter(prefix="/api/v1/sessions", tags=["Sessions"])
alerts_router = APIRouter(prefix="/api/v1/alerts", tags=["Sessions"])
GPS_FEED_KEYS = ("position_tracking", "gpsloc")

logger = logging.getLogger(__name__)


def _refresh_session_gate() -> None:
    """Tell the ingestion transports the session landscape changed.

    Imported lazily so importing this router does not pull in the transport stack, and
    guarded because a gate refresh is an optimisation: failing to wake a transport must
    never fail the lifecycle request the operator just made.
    """
    try:
        from app.services.session_gate import GATE

        GATE.refresh()
    except Exception:
        logger.exception("session gate refresh failed after a lifecycle change")


def _warm_iot_for_session(session_id: str) -> None:
    """Background task: seed a freshly started session with current telemetry."""
    from app.services.iot_live import refresh_now

    stored = refresh_now(reason="session_start:{}".format(session_id))
    logger.info("Session %s warm-up stored %s reading(s)", session_id, stored)


def _assert_session_access(session: OperationSession, user: User, db: Session) -> None:
    if user.role == "owner":
        return
    if user.role == "researcher":
        return
    if session.operator_id == user.id or session.client_farmer_id == user.id:
        return
    if session.tractor_owner_id == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have access to this session",
    )


def _to_session_response(obj: OperationSession) -> SessionResponse:
    payload = SessionResponse.model_validate(obj).model_dump()
    payload["operator_name"] = obj.operator.name if getattr(obj, "operator", None) is not None else None
    tractor = getattr(obj, "tractor", None)
    if tractor is not None:
        payload["tractor_name"] = tractor.name
    alerts = getattr(obj, "alerts", None)
    if alerts is not None:
        payload["alerts_count"] = len(alerts)
        payload["unacknowledged_alerts"] = sum(1 for alert in alerts if not alert.acknowledged)
    return SessionResponse(**payload)


def _severity_to_status(severity_color: Optional[str]) -> Optional[str]:
    if severity_color == "red":
        return "critical"
    if severity_color in ("orange", "yellow"):
        return "warning"
    return None


def _to_utc(dt: datetime) -> datetime:
    """Normalize datetime to UTC-aware for safe arithmetic."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _worked_duration_minutes(session: OperationSession, db: Session) -> float:
    """Minutes actually worked: wall clock minus every paused interval.

    The same quantity Threshing/Grading are billed on, so the duration a farmer reads on
    the summary is the duration they were charged for. An in-progress session is measured
    to now.
    """
    from app.services.operation_cost_service import paused_seconds

    start = _to_utc(session.started_at)
    end = _to_utc(session.ended_at) if session.ended_at else datetime.now(timezone.utc)
    elapsed = (end - start).total_seconds()
    paused = float(paused_seconds(session.id, db, until=session.ended_at))
    return max(0.0, (elapsed - paused) / 60.0)


def _backfill_legacy_billing(session: OperationSession, db: Session) -> None:
    """Close out a terminated session that predates the finalize-once billing path.

    Deliberately narrow: it runs only when ``cost_finalized_at`` is NULL, i.e. the charge
    was never issued. A session whose charge *is* final is never recomputed -- that used
    to happen on every summary read and silently re-billed finished sessions at whatever
    the owner's current rate happened to be.
    """
    if session.cost_finalized_at is not None:
        return
    if session.status not in ("completed", "aborted"):
        return

    from app.services.field_area_service import finalize_session_area
    from app.services.operation_cost_service import (
        finalize_cancelled_session,
        finalize_session_billing,
    )

    if session.status == "aborted":
        finalize_cancelled_session(session, db)
    else:
        if session.area_ha is None:
            finalize_session_area(session.id, db)
            db.refresh(session)
        finalize_session_billing(session, db)
    db.commit()
    db.refresh(session)


def _extract_lat_lon(raw_value: str) -> tuple[Optional[float], Optional[float]]:
    try:
        parsed = json.loads(raw_value)
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        lat = parsed.get("lat")
        lon = parsed.get("lon")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
        lat = parsed.get("latitude")
        lon = parsed.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    elif isinstance(parsed, list) and len(parsed) >= 2:
        return float(parsed[0]), float(parsed[1])

    if "," in raw_value:
        parts = [p.strip() for p in raw_value.split(",")]
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                pass
    return None, None


@router.post("/start", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def start_session(
    body: SessionStartRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    tractor_id = uuid.UUID(body.tractor_id)
    tractor = db.get(Tractor, tractor_id)
    if tractor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")

    active_exists = db.scalars(
        select(OperationSession).where(
            OperationSession.operator_id == current_user.id,
            OperationSession.status.in_(("active", "paused")),
        )
    ).first()
    if active_exists is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operator already has an active session",
        )

    if not body.client_farmer_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Farmer selection is required to start a session",
        )

    try:
        farmer_uuid = uuid.UUID(body.client_farmer_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid farmer id",
        ) from exc

    farmer = db.get(User, farmer_uuid)
    if farmer is None or farmer.role != "farmer" or not farmer.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Selected farmer not found",
        )

    implement_width_m: Optional[float] = None
    implement_uuid: Optional[uuid.UUID] = None
    implement: Optional[Implement] = None
    if body.implement_id:
        implement_uuid = uuid.UUID(body.implement_id)
        implement = db.get(Implement, implement_uuid)
        if implement is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Implement not found")
        # `Implement.resolved_width_m` is the one width-resolution rule every
        # subsystem shares (working_width_m first, width as fallback) -- see
        # its docstring for why this used to be duplicated here independently
        # of the simulation engine's own (different) rule.
        raw_width = implement.resolved_width_m
        if raw_width is not None:
            implement_width_m = float(raw_width)

    owner_id = getattr(tractor, "owner_id", None)
    session = OperationSession(
        tractor_id=tractor.id,
        implement_id=implement_uuid,
        operator_id=current_user.id,
        tractor_owner_id=owner_id,
        client_farmer_id=farmer_uuid,
        operation_type=body.operation_type,
        gps_tracking_enabled=body.gps_tracking_enabled,
        implement_width_m=implement_width_m,
        status="active",
    )

    # Lock the owner's rate onto the session before it exists. Two things follow: the
    # charge can never be re-derived from a rate card the owner edits later, and a session
    # that could not be priced is refused up front rather than discovered to be unbillable
    # once the work is already done.
    try:
        lock_session_rate(session, owner_id=owner_id, db=db)
    except RateNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.reason,
        ) from exc

    db.add(session)
    db.flush()

    auto_presets: list[SessionPresetValue] = []
    if implement is not None:
        if implement.preset_speed_kmh is not None:
            auto_presets.append(
                SessionPresetValue(
                    session_id=session.id,
                    parameter_name="forward_speed",
                    required_value=implement.preset_speed_kmh,
                    unit="km/h",
                    deviation_pct_warn=10.0,
                    deviation_pct_crit=25.0,
                )
            )
        if implement.preset_depth_cm is not None:
            auto_presets.append(
                SessionPresetValue(
                    session_id=session.id,
                    parameter_name="operation_depth",
                    required_value=implement.preset_depth_cm,
                    unit="cm",
                    deviation_pct_warn=10.0,
                    deviation_pct_crit=25.0,
                )
            )
        if implement.preset_gearbox_temp_max_c is not None:
            auto_presets.append(
                SessionPresetValue(
                    session_id=session.id,
                    parameter_name="gearbox_temperature",
                    required_value=implement.preset_gearbox_temp_max_c,
                    unit="\u00B0C",
                    deviation_pct_warn=10.0,
                    deviation_pct_crit=25.0,
                )
            )

    for preset in auto_presets:
        db.add(preset)

    db.commit()
    db.refresh(session)

    # Wake the transports synchronously: the operator is about to watch this session, so
    # the MQTT subscriber should be connecting before the response is even rendered.
    _refresh_session_gate()

    # Pull telemetry immediately so the Active Session screen has live values on its first render
    # rather than waiting out a poll interval. Runs after the response; failures are logged only.
    background_tasks.add_task(_warm_iot_for_session, str(session.id))
    return _to_session_response(session)


@router.post("/{session_id}/stop", response_model=SessionResponse)
def stop_session(
    session_id: uuid.UUID,
    _: SessionStopRequest,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this session")
    if session.status not in ("active", "paused"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active or paused")

    from app.services.field_area_service import finalize_session_area
    from app.services.ingest_buffer import BUFFER
    from app.services.operation_cost_service import close_open_pause, finalize_session_billing
    from app.services.session_gate import GATE

    # 1. Flush the ingestion buffer FIRST, while the session is still `active`/`paused`
    #    so buffered readings still resolve to it. Ordering is load-bearing: once the
    #    status flips to "completed" it leaves ATTACHABLE_SESSION_STATUSES, those
    #    readings attach to nothing, and they vanish from the GPS path -- and therefore
    #    from the worked area, and therefore from the bill.
    BUFFER.flush()

    # 2. Only now close the session. The explicit flush replaces an accidental one:
    #    `SessionLocal` is autoflush=False, and the `db.refresh` below used to depend on
    #    `finalize_session_area` happening to call `db.flush()` inside another module.
    #    Stopping straight from `paused` closes the open pause interval at the same
    #    instant, so the trailing pause is neither billed nor left dangling.
    ended_at = datetime.now(timezone.utc)
    close_open_pause(session, db, at=ended_at)
    session.ended_at = ended_at
    session.status = "completed"
    db.flush()

    # 3. Area and cost, computed over a complete set of readings. This is the one and only
    #    place a completed session's charge is written; read paths report it, never
    #    recompute it.
    finalize_session_area(session_id, db)
    db.refresh(session)
    finalize_session_billing(session, db)
    db.commit()
    db.refresh(session)
    response = _to_session_response(session)

    # 4. Last statement, after the commit: let the transports go dormant and the
    #    connection pool dispose if nothing else is running.
    GATE.refresh()
    return response


@router.patch("/{session_id}/pause", response_model=SessionResponse)
def pause_session(
    session_id: uuid.UUID,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this session")
    if session.status != "active":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only active sessions can be paused")

    # Open a pause interval. Billing reads this ledger twice: to net idle time out of the
    # hours a Threshing/Grading session is charged for, and to drop GPS fixes recorded
    # while paused so transit between fields is not billed as hectares covered.
    session.status = "paused"
    db.add(SessionPause(session_id=session.id, paused_at=datetime.now(timezone.utc)))
    db.commit()
    db.refresh(session)
    response = _to_session_response(session)
    # The gate stays OPEN while paused -- telemetry must keep attaching so the GPS trail
    # stays continuous for finalize_session_area. Refreshing anyway keeps it honest.
    _refresh_session_gate()
    return response


@router.patch("/{session_id}/resume", response_model=SessionResponse)
def resume_session(
    session_id: uuid.UUID,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this session")
    if session.status != "paused":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only paused sessions can be resumed")

    from app.services.operation_cost_service import close_open_pause

    close_open_pause(session, db, at=datetime.now(timezone.utc))
    session.status = "active"
    db.commit()
    db.refresh(session)
    response = _to_session_response(session)
    _refresh_session_gate()
    return response


@router.post("/{session_id}/cancel", response_model=SessionResponse)
def cancel_session(
    session_id: uuid.UUID,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    """Abandon a session without billing it.

    A session started by mistake previously had no exit but `stop`, which issues a charge.
    Cancelling terminates it at a final zero: the status becomes `aborted`, the charge is
    finalized at Rs 0.00 so nothing can later re-price it, and it drops out of the owner's
    charge totals. Area is still finalized so the map and the telemetry record stay
    intact.
    """
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this session")
    if session.status not in ("active", "paused"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not active or paused")

    from app.services.field_area_service import finalize_session_area
    from app.services.ingest_buffer import BUFFER
    from app.services.operation_cost_service import close_open_pause, finalize_cancelled_session
    from app.services.session_gate import GATE

    # Same ordering rationale as `stop_session`: flush buffered readings while the session
    # is still attachable, so the recorded trail is complete even though it is not billed.
    BUFFER.flush()

    ended_at = datetime.now(timezone.utc)
    close_open_pause(session, db, at=ended_at)
    session.ended_at = ended_at
    session.status = "aborted"
    db.flush()

    finalize_session_area(session_id, db)
    db.refresh(session)
    finalize_cancelled_session(session, db)
    db.commit()
    db.refresh(session)
    response = _to_session_response(session)

    GATE.refresh()
    return response


@router.get("/active", response_model=list[SessionResponse])
def list_active_sessions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(OperationSession).where(OperationSession.status.in_(("active", "paused")))
    if current_user.role == "operator":
        stmt = stmt.where(OperationSession.operator_id == current_user.id)
    elif current_user.role == "owner":
        pass
    elif current_user.role == "farmer":
        stmt = stmt.where(OperationSession.client_farmer_id == current_user.id)
    elif current_user.role == "researcher":
        pass
    else:
        return []
    rows = db.scalars(
        stmt.options(
            selectinload(OperationSession.operator),
            selectinload(OperationSession.tractor),
            selectinload(OperationSession.alerts),
        ).order_by(OperationSession.started_at.desc())
    ).all()
    return [_to_session_response(r) for r in rows]


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.scalars(
        select(OperationSession)
        .where(OperationSession.id == session_id)
        .options(
            selectinload(OperationSession.operator),
            selectinload(OperationSession.tractor),
            selectinload(OperationSession.preset_values),
            selectinload(OperationSession.alerts),
            selectinload(OperationSession.field_observations),
        )
    ).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _assert_session_access(session, current_user, db)

    return SessionDetailResponse(
        **_to_session_response(session).model_dump(),
        preset_values=[PresetValueResponse.model_validate(p) for p in session.preset_values],
        alerts=[AlertResponse.model_validate(a) for a in session.alerts],
        field_observations=[FieldObservationResponse.model_validate(o) for o in session.field_observations],
        total_duration_minutes=_worked_duration_minutes(session, db),
    )


@router.get("/", response_model=list[SessionResponse])
def list_sessions(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(OperationSession).options(
        selectinload(OperationSession.operator),
        selectinload(OperationSession.tractor),
        selectinload(OperationSession.alerts),
    )
    if current_user.role == "operator":
        stmt = stmt.where(OperationSession.operator_id == current_user.id)
    elif current_user.role == "owner":
        pass
    elif current_user.role == "farmer":
        stmt = stmt.where(OperationSession.client_farmer_id == current_user.id)
    elif current_user.role == "researcher":
        pass
    else:
        return []

    if status_filter:
        stmt = stmt.where(OperationSession.status == status_filter)
    if start_date:
        stmt = stmt.where(OperationSession.started_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(OperationSession.started_at <= datetime.combine(end_date, datetime.max.time()))

    rows = db.scalars(
        stmt.order_by(OperationSession.started_at.desc()).limit(limit).offset(offset)
    ).all()
    return [_to_session_response(r) for r in rows]


@router.post("/{session_id}/observations", response_model=FieldObservationResponse, status_code=status.HTTP_201_CREATED)
def create_observation(
    session_id: uuid.UUID,
    body: FieldObservationCreate,
    current_user: User = Depends(require_role(["operator"])),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if session.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own this session")

    lat = body.lat
    lon = body.lon
    if lat is None:
        latest_gps = db.scalars(
            select(IoTReading)
            .where(
                and_(
                    IoTReading.session_id == session_id,
                    or_(
                        IoTReading.feed_key == GPS_FEED_KEYS[0],
                        IoTReading.feed_key == GPS_FEED_KEYS[1],
                    ),
                )
            )
            .order_by(IoTReading.device_timestamp.desc())
            .limit(1)
        ).first()
        if latest_gps is not None:
            parsed_lat, parsed_lon = _extract_lat_lon(latest_gps.raw_value)
            if parsed_lat is not None:
                lat = parsed_lat
            if lon is None and parsed_lon is not None:
                lon = parsed_lon

    obs = FieldObservation(
        session_id=session_id,
        obs_type=body.obs_type,
        value=body.value,
        unit=body.unit,
        lat=lat,
        lon=lon,
        notes=body.notes,
        recorded_by=current_user.id,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return FieldObservationResponse.model_validate(obs)


@router.get("/{session_id}/observations", response_model=list[FieldObservationResponse])
def list_observations(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _assert_session_access(session, current_user, db)

    rows = db.scalars(
        select(FieldObservation)
        .where(FieldObservation.session_id == session_id)
        .order_by(FieldObservation.recorded_at.desc())
    ).all()
    return [FieldObservationResponse.model_validate(r) for r in rows]


@router.get("/{session_id}/gps-path")
def get_session_gps_path(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _assert_session_access(session, current_user, db)

    rows = db.scalars(
        select(IoTReading)
        .where(
            and_(
                IoTReading.session_id == session_id,
                or_(
                    IoTReading.feed_key == GPS_FEED_KEYS[0],
                    IoTReading.feed_key == GPS_FEED_KEYS[1],
                ),
            )
        )
        .order_by(IoTReading.device_timestamp.asc())
    ).all()

    points: list[dict] = []
    for reading in rows:
        lat: Optional[float] = None
        lon: Optional[float] = None

        try:
            parsed = json.loads(reading.raw_value)
            if isinstance(parsed, dict):
                lat_raw = parsed["lat"]
                lon_raw = parsed["lon"]
                lat = float(lat_raw)
                lon = float(lon_raw)
        except Exception:
            lat_raw = getattr(reading, "lat", None)
            lon_raw = getattr(reading, "lon", None)
            if lat_raw is None:
                lat_raw = getattr(reading, "latitude", None)
            if lon_raw is None:
                lon_raw = getattr(reading, "longitude", None)
            if lat_raw is not None and lon_raw is not None:
                try:
                    lat = float(lat_raw)
                    lon = float(lon_raw)
                except (TypeError, ValueError):
                    lat = None
                    lon = None

        if lat is None or lon is None:
            continue
        if math.isnan(lat) or math.isnan(lon):
            continue

        points.append(
            {
                "lat": lat,
                "lon": lon,
                "timestamp": reading.device_timestamp.isoformat(),
            }
        )

    return {
        "session_id": str(session_id),
        "points": points,
        "total_points": len(points),
        "area_ha": float(session.area_ha) if session.area_ha is not None else None,
        "implement_width_m": float(session.implement_width_m) if session.implement_width_m is not None else None,
    }


@router.get("/{session_id}/area-summary")
def get_session_area_summary(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(OperationSession, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _assert_session_access(session, current_user, db)

    total_gps_points = db.scalar(
        select(func.count())
        .select_from(IoTReading)
        .where(
            and_(
                IoTReading.session_id == session_id,
                or_(
                    IoTReading.feed_key == GPS_FEED_KEYS[0],
                    IoTReading.feed_key == GPS_FEED_KEYS[1],
                ),
            )
        )
    ) or 0

    # Only ever closes out a session whose charge was never issued (legacy rows). A
    # finalized session is reported as-is: this endpoint used to recompute the cost on
    # every poll, which is one of the paths that let a rate edit rewrite a finished bill.
    _backfill_legacy_billing(session, db)

    duration_minutes: Optional[float] = None
    if session.started_at is not None:
        duration_minutes = _worked_duration_minutes(session, db)

    return {
        "session_id": str(session_id),
        "area_ha": float(session.area_ha) if session.area_ha is not None else None,
        "implement_width_m": float(session.implement_width_m) if session.implement_width_m is not None else None,
        "total_gps_points": int(total_gps_points),
        "session_duration_minutes": duration_minutes,
        "billable_hours": float(session.billable_hours) if session.billable_hours is not None else None,
        "charge_unit": session.charge_unit,
        "operation_type": session.operation_type,
        "status": session.status,
    }


@alerts_router.get("", response_model=AlertListResponse)
def list_alerts(
    session_id: Optional[uuid.UUID] = Query(default=None),
    acknowledged: Optional[bool] = Query(default=None),
    severity_color: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(IoTAlert)
        .outerjoin(OperationSession, IoTAlert.session_id == OperationSession.id)
        .order_by(IoTAlert.created_at.desc())
    )

    if current_user.role == "operator":
        stmt = stmt.where(OperationSession.operator_id == current_user.id)
    elif current_user.role == "farmer":
        stmt = stmt.where(OperationSession.client_farmer_id == current_user.id)
    elif current_user.role == "owner":
        pass
    elif current_user.role == "researcher":
        pass
    else:
        return AlertListResponse(total=0, items=[])

    if session_id is not None:
        stmt = stmt.where(IoTAlert.session_id == session_id)
    if acknowledged is not None:
        stmt = stmt.where(IoTAlert.acknowledged == acknowledged)

    mapped_status = _severity_to_status(severity_color)
    if mapped_status is not None:
        stmt = stmt.where(IoTAlert.alert_status == mapped_status)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    return AlertListResponse(
        total=int(total),
        items=[AlertResponse.model_validate(row) for row in rows],
    )


@alerts_router.patch("/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(
    alert_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    alert = db.get(IoTAlert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    if alert.session_id is not None:
        session = db.get(OperationSession, alert.session_id)
        if session is not None:
            _assert_session_access(session, current_user, db)

    alert.acknowledged = True
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = current_user.id
    db.commit()
    db.refresh(alert)
    return AlertResponse.model_validate(alert)


_combined_router = APIRouter()
_combined_router.include_router(router)
_combined_router.include_router(alerts_router)
router = _combined_router

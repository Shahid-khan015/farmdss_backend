from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db
from app.middleware.auth import get_current_user
from app.models.iot_reading import IoTReading
from app.models.session import IoTAlert, OperationSession
from app.models.user import User
from app.routes.sessions import _assert_session_access, _to_utc
from app.schemas.session import (
    AlertSummaryItem,
    FieldObservationResponse,
    PresetSummaryItem,
    SessionMetricSummary,
    SessionSummaryReport,
)
from app.services.report_service import ReportFilters, generate_report
from app.services.field_area_service import finalize_session_area, parse_gps_points, compute_total_path_distance_m
from app.services.operation_cost_service import (
    compute_session_cost,
    resolve_session_billing,
    session_billing_differs_from_persisted,
)
from app.services.export_service import build_csv_bytes, build_pdf_bytes

router = APIRouter(prefix="/api/v1/reports", tags=["Reports"])

SESSION_METRIC_LABELS = {
    "forward_speed": ("Speed", "km/h"),
    "pto_shaft_speed": ("PTO Speed", "rpm"),
    "depth_of_operation": ("Depth", "cm"),
    "soil_moisture": ("Soil Moisture", "%"),
    "field_capacity": ("Field Capacity", "ha/h"),
    "wheel_slip": ("Wheel Slip", "%"),
    "gearbox_temperature": ("Gearbox Temperature", "\u00B0C"),
    "vibration": ("Vibration", ""),
}

PRESET_TO_FEED = {
    "forward_speed": "forward_speed",
    "operation_depth": "depth_of_operation",
    "pto_shaft_speed": "pto_shaft_speed",
    "gearbox_temperature": "gearbox_temperature",
    "wheel_slip": "wheel_slip",
    "soil_moisture": "soil_moisture",
    "field_capacity": "field_capacity",
    "vibration_level": "vibration",
}


def _to_utc_opt(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/summary")
def get_report_summary(
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    operation_type: Optional[str] = Query(default=None),
    tractor_id: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if start_date is not None:
        try:
            start_dt = datetime.strptime(
                f"{start_date} {start_time or '00:00'}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date must be YYYY-MM-DD and start_time must be HH:MM",
            ) from exc

    if end_date is not None:
        try:
            end_dt = datetime.strptime(
                f"{end_date} {end_time or '23:59'}", "%Y-%m-%d %H:%M"
            ).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="end_date must be YYYY-MM-DD and end_time must be HH:MM",
            ) from exc

    if start_dt is not None and end_dt is not None and end_dt < start_dt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end datetime must be greater than or equal to start datetime",
        )

    tractor_uuid: Optional[UUID] = None
    if tractor_id is not None:
        try:
            tractor_uuid = UUID(tractor_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tractor_id must be a valid UUID",
            ) from exc

    filters = ReportFilters(
        owner_id=current_user.id if current_user.role == "owner" else None,
        operator_id=current_user.id if current_user.role == "operator" else None,
        client_farmer_id=current_user.id if current_user.role == "farmer" else None,
        tractor_id=tractor_uuid,
        start_datetime=start_dt,
        end_datetime=end_dt,
        operation_type=operation_type,
    )
    return generate_report(filters, db)


@router.get("/session/{session_id}", response_model=SessionSummaryReport)
def get_session_summary_report(
    session_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.scalars(
        select(OperationSession)
        .where(OperationSession.id == session_id)
        .options(
            selectinload(OperationSession.operator),
            selectinload(OperationSession.preset_values),
            selectinload(OperationSession.field_observations),
        )
    ).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    _assert_session_access(session, current_user, db)

    if session.status == "completed" and session.area_ha is None:
        finalize_session_area(session_id, db)
        db.commit()
        db.refresh(session)

    billing = resolve_session_billing(session, db)
    if session.status == "completed" and session.area_ha is not None:
        if session_billing_differs_from_persisted(session, billing):
            compute_session_cost(session, db)
            db.commit()
            db.refresh(session)
            billing = resolve_session_billing(session, db)

    alerts = list(
        db.scalars(
            select(IoTAlert)
            .where(IoTAlert.session_id == session_id)
            .order_by(IoTAlert.created_at.desc())
        ).all()
    )

    duration_minutes: Optional[float] = None
    if session.started_at is not None:
        end_dt = _to_utc(session.ended_at) if session.ended_at is not None else datetime.now(timezone.utc)
        duration_minutes = max(0.0, (end_dt - _to_utc(session.started_at)).total_seconds() / 60.0)

    session_start = _to_utc_opt(session.started_at) or datetime.now(timezone.utc)
    session_end = _to_utc_opt(session.ended_at) or datetime.now(timezone.utc)

    # Primary query: filter strictly by session_id (most reliable).
    # Do NOT additionally restrict by device_timestamp here — small device-clock
    # offsets can put valid readings just outside the session window.
    readings = list(
        db.scalars(
            select(IoTReading)
            .where(IoTReading.session_id == session_id)
            .order_by(IoTReading.device_timestamp.asc())
        ).all()
    )

    if not readings:
        # Fallback: readings ingested before session was active (no session_id set).
        # Use the time window to scope them — but only as a best-effort fallback.
        readings = list(
            db.scalars(
                select(IoTReading)
                .where(
                    IoTReading.device_timestamp >= session_start,
                    IoTReading.device_timestamp <= session_end,
                )
                .order_by(IoTReading.device_timestamp.asc())
            ).all()
        )

    metric_groups: dict[str, list[IoTReading]] = {}
    for reading in readings:
        metric_groups.setdefault(reading.feed_key, []).append(reading)

    metric_items: list[SessionMetricSummary] = []
    for feed_key, rows in metric_groups.items():
        numeric_values = [float(row.numeric_value) for row in rows if row.numeric_value is not None]
        label, fallback_unit = SESSION_METRIC_LABELS.get(
            feed_key,
            (feed_key.replace("_", " ").title(), rows[-1].unit if rows else ""),
        )
        unit = rows[-1].unit if rows and rows[-1].unit else fallback_unit
        metric_items.append(
            SessionMetricSummary(
                feed_key=feed_key,
                label=label,
                unit=unit,
                samples=len(rows),
                last_value=numeric_values[-1] if numeric_values else None,
                avg_value=(sum(numeric_values) / len(numeric_values)) if numeric_values else None,
                min_value=min(numeric_values) if numeric_values else None,
                max_value=max(numeric_values) if numeric_values else None,
            )
        )

    metrics_by_feed = {metric.feed_key: metric for metric in metric_items}
    preset_summaries: list[PresetSummaryItem] = []
    for preset in session.preset_values:
        mapped_feed = PRESET_TO_FEED.get(preset.parameter_name)
        metric = metrics_by_feed.get(mapped_feed) if mapped_feed else None
        actual_value = metric.avg_value if metric and metric.avg_value is not None else metric.last_value if metric else None
        deviation_pct: Optional[float] = None
        status_label = "unknown"
        if (
            actual_value is not None
            and preset.required_value is not None
            and preset.required_value != 0
        ):
            deviation_pct = abs(actual_value - preset.required_value) / abs(preset.required_value) * 100.0
            if deviation_pct >= float(preset.deviation_pct_crit or 25.0):
                status_label = "critical"
            elif deviation_pct >= float(preset.deviation_pct_warn or 10.0):
                status_label = "warning"
            else:
                status_label = "ok"
        preset_summaries.append(
            PresetSummaryItem(
                parameter_name=preset.parameter_name,
                target_value=preset.required_value,
                actual_value=actual_value,
                unit=preset.unit,
                deviation_pct=round(deviation_pct, 2) if deviation_pct is not None else None,
                status=status_label,
            )
        )

    def severity_color_for(alert: IoTAlert) -> Optional[str]:
        if alert.alert_status == "critical":
            return "red"
        if alert.alert_status == "warning":
            return "orange"
        return None

    alert_items = [
        AlertSummaryItem(
            id=alert.id,
            feed_key=alert.feed_key,
            alert_type=alert.alert_type,
            alert_status=alert.alert_status,
            severity_color=severity_color_for(alert),
            actual_value=alert.actual_value,
            message=alert.message,
            acknowledged=alert.acknowledged,
            created_at=alert.created_at,
        )
        for alert in alerts
    ]

    gps_points = parse_gps_points(session_id, db)
    total_distance_m: Optional[float] = None
    if gps_points:
        raw_dist = compute_total_path_distance_m(gps_points)
        total_distance_m = round(raw_dist, 1)

    return SessionSummaryReport(
        session_id=str(session.id),
        operation_type=session.operation_type,
        status=session.status,
        tractor_id=str(session.tractor_id),
        implement_id=str(session.implement_id) if session.implement_id else None,
        operator_id=str(session.operator_id),
        operator_name=session.operator.name if session.operator is not None else None,
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_minutes=duration_minutes,
        area_ha=session.area_ha,
        total_distance_m=total_distance_m,
        total_cost_inr=billing.total_cost_inr,
        charge_per_ha_applied=billing.charge_per_ha_applied,
        cost_note=billing.cost_note,
        alerts=alert_items,
        field_observations=[
            FieldObservationResponse.model_validate(observation)
            for observation in session.field_observations
        ],
        observations_count=len(session.field_observations),
        metrics=metric_items,
        preset_summaries=preset_summaries,
        total_alerts=len(alert_items),
        unacknowledged_alerts=sum(1 for alert in alerts if not alert.acknowledged),
    )


@router.get("/session/{session_id}/export")
def export_session_report(
    session_id: UUID,
    format: str = Query(default="csv", description="Export format: csv or pdf"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a session report as CSV or PDF."""
    if format not in ("csv", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="format must be 'csv' or 'pdf'",
        )

    # Reuse the existing report builder by calling it directly
    report: SessionSummaryReport = get_session_summary_report(
        session_id=session_id,
        db=db,
        current_user=current_user,
    )

    safe_id = str(session_id)[:8]
    if format == "csv":
        data = build_csv_bytes(report)
        filename = f"session_{safe_id}.csv"
        media_type = "text/csv"
    else:
        try:
            data = build_pdf_bytes(report)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        filename = f"session_{safe_id}.pdf"
        media_type = "application/pdf"

    return StreamingResponse(
        iter([data]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

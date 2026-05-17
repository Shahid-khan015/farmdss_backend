from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.session import FuelLog, IoTAlert, OperationSession, WageRecord
from app.models.tractor import Tractor
from app.models.user import User


@dataclass
class ReportFilters:
    owner_id: Optional[UUID] = None
    operator_id: Optional[UUID] = None
    client_farmer_id: Optional[UUID] = None
    tractor_id: Optional[UUID] = None
    start_datetime: Optional[datetime] = None
    end_datetime: Optional[datetime] = None
    operation_type: Optional[str] = None


def _finalize_completed_sessions_for_report(session_ids: list[UUID], db: Session) -> None:
    from app.services.field_area_service import finalize_session_area
    from app.services.operation_cost_service import compute_session_cost

    sessions = list(
        db.scalars(
            select(OperationSession).where(
                OperationSession.id.in_(session_ids),
                OperationSession.status == "completed",
                or_(
                    OperationSession.area_ha.is_(None),
                    OperationSession.total_cost_inr.is_(None),
                    OperationSession.charge_per_ha_applied.is_(None),
                ),
            )
        ).all()
    )
    if not sessions:
        return

    for session in sessions:
        if session.area_ha is None:
            finalize_session_area(session.id, db)
            db.refresh(session)
        compute_session_cost(session, db)
    db.commit()


def _apply_filters(stmt, filters: ReportFilters):
    if filters.owner_id is not None:
        owner_tractor_ids = select(Tractor.id).where(Tractor.owner_id == filters.owner_id)
        stmt = stmt.where(
            or_(
                OperationSession.tractor_owner_id == filters.owner_id,
                OperationSession.tractor_id.in_(owner_tractor_ids),
            )
        )
    if filters.operator_id is not None:
        stmt = stmt.where(OperationSession.operator_id == filters.operator_id)
    if filters.client_farmer_id is not None:
        stmt = stmt.where(OperationSession.client_farmer_id == filters.client_farmer_id)
    if filters.tractor_id is not None:
        stmt = stmt.where(OperationSession.tractor_id == filters.tractor_id)
    if filters.operation_type is not None:
        stmt = stmt.where(OperationSession.operation_type == filters.operation_type)
    if filters.start_datetime is not None:
        stmt = stmt.where(
            func.coalesce(OperationSession.ended_at, func.now()) >= filters.start_datetime
        )
    if filters.end_datetime is not None:
        stmt = stmt.where(OperationSession.started_at <= filters.end_datetime)
    return stmt


def generate_report(filters: ReportFilters, db: Session) -> dict:
    session_ids = list(db.scalars(_apply_filters(select(OperationSession.id), filters)).all())
    if not session_ids:
        return {
            "total_sessions": 0,
            "total_area_ha": 0.0,
            "total_duration_hours": 0.0,
            "total_wages_paid": 0.0,
            "total_operation_charges": 0.0,
            "total_fuel_litres": 0.0,
            "total_fuel_cost": 0.0,
            "alert_counts": {"warning": 0, "critical": 0},
            "sessions": [],
        }

    _finalize_completed_sessions_for_report(session_ids, db)
    base = select(OperationSession.id).where(OperationSession.id.in_(session_ids)).subquery()

    duration_hours_expr = (
        func.extract("epoch", func.coalesce(OperationSession.ended_at, OperationSession.started_at) - OperationSession.started_at)
        / 3600.0
    )

    summary_stmt = (
        select(
            func.count(OperationSession.id),
            func.coalesce(func.sum(OperationSession.area_ha), 0.0),
            func.coalesce(func.sum(duration_hours_expr), 0.0),
        )
        .where(OperationSession.id.in_(select(base.c.id)))
    )
    total_sessions, total_area_ha, total_duration_hours = db.execute(summary_stmt).one()

    charges_stmt = select(
        func.coalesce(
            func.sum(
                func.coalesce(OperationSession.total_cost_inr, WageRecord.total_amount, 0.0)
            ),
            0.0,
        )
    ).outerjoin(
        WageRecord,
        WageRecord.session_id == OperationSession.id,
    ).where(
        OperationSession.id.in_(select(base.c.id)),
    )
    total_operation_charges = db.scalar(charges_stmt) or 0.0

    fuel_stmt = select(
        func.coalesce(func.sum(FuelLog.litres), 0.0),
        func.coalesce(func.sum(FuelLog.total_cost), 0.0),
    ).where(FuelLog.session_id.in_(select(base.c.id)))
    total_fuel_litres, total_fuel_cost = db.execute(fuel_stmt).one()

    alerts_stmt = (
        select(
            IoTAlert.alert_status,
            func.count(IoTAlert.id),
        )
        .where(IoTAlert.session_id.in_(select(base.c.id)))
        .group_by(IoTAlert.alert_status)
    )
    alert_counts = {"warning": 0, "critical": 0}
    for status_value, count_value in db.execute(alerts_stmt).all():
        if status_value in alert_counts:
            alert_counts[status_value] = int(count_value or 0)

    sessions_stmt = (
        select(
            OperationSession.id,
            OperationSession.started_at,
            OperationSession.ended_at,
            OperationSession.operation_type,
            OperationSession.area_ha,
            User.name,
            WageRecord.total_amount,
            OperationSession.total_cost_inr,
        )
        .join(User, User.id == OperationSession.operator_id)
        .outerjoin(WageRecord, WageRecord.session_id == OperationSession.id)
        .where(OperationSession.id.in_(select(base.c.id)))
        .order_by(OperationSession.started_at.desc())
        .limit(100)
    )
    session_rows = db.execute(sessions_stmt).all()
    sessions = []
    for row in session_rows:
        sessions.append(
            {
                "id": str(row[0]),
                "started_at": row[1].isoformat() if row[1] else None,
                "ended_at": row[2].isoformat() if row[2] else None,
                "operation_type": row[3],
                "area_ha": float(row[4]) if row[4] is not None else None,
                "operator_name": row[5],
                "wage_total_amount": float(row[6]) if row[6] is not None else None,
                "total_cost_inr": float(row[7]) if row[7] is not None else None,
            }
        )

    return {
        "total_sessions": int(total_sessions or 0),
        "total_area_ha": float(total_area_ha or 0.0),
        "total_duration_hours": float(total_duration_hours or 0.0),
        "total_wages_paid": float(total_operation_charges or 0.0),
        "total_operation_charges": float(total_operation_charges or 0.0),
        "total_fuel_litres": float(total_fuel_litres or 0.0),
        "total_fuel_cost": float(total_fuel_cost or 0.0),
        "alert_counts": alert_counts,
        "sessions": sessions,
    }

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.session import OperationSession, WageRecord
from app.models.user import User


def compute_wage(session_id: uuid.UUID, db: Session) -> WageRecord:
    session = db.get(OperationSession, session_id)
    if session is None:
        raise ValueError("Session not found")
    if session.status != "completed":
        raise ValueError("Session must be completed to compute wage")

    existing = db.query(WageRecord).filter(WageRecord.session_id == session_id).first()
    if existing is not None:
        return existing

    operator = db.get(User, session.operator_id)
    if operator is None:
        raise ValueError("Operator not found")

    profile = operator.profile
    per_ha_rate = profile.wage_rate_per_hectare if profile else None
    per_hour_rate = profile.wage_rate_per_hour if profile else None

    if session.ended_at is None or session.started_at is None:
        raise ValueError("Session is missing started_at or ended_at")

    duration_hours = (session.ended_at - session.started_at).total_seconds() / 3600.0

    if session.area_ha is not None and per_ha_rate is not None:
        rate_type = "per_ha"
        rate_amount = float(per_ha_rate)
        total_amount = round(rate_amount * session.area_ha, 2)
    elif per_hour_rate is not None:
        rate_type = "hourly"
        rate_amount = float(per_hour_rate)
        total_amount = round(rate_amount * duration_hours, 2)
    else:
        raise ValueError("Operator has no wage rate configured in their profile.")

    record = WageRecord(
        session_id=session.id,
        operator_id=session.operator_id,
        rate_type=rate_type,
        rate_amount=rate_amount,
        area_ha=session.area_ha,
        duration_hours=duration_hours,
        total_amount=total_amount,
        approved=False,
        disputed=False,
    )
    db.add(record)
    db.flush()
    return record

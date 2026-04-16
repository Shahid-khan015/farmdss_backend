from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.operation_charge import OperationCharge
from app.models.session import OperationSession
from app.models.tractor import Tractor

logger = logging.getLogger(__name__)

PER_HOUR_OPERATION_TYPES = frozenset({"threshing", "grading"})


@dataclass(frozen=True)
class SessionBilling:
    total_cost_inr: Optional[float]
    charge_per_ha_applied: Optional[float]
    cost_note: Optional[str]


def _session_duration_hours(session: OperationSession) -> Optional[float]:
    """Wall-clock hours from started_at to ended_at (session must be completed)."""
    if session.started_at is None or session.ended_at is None:
        return None
    secs = (session.ended_at - session.started_at).total_seconds()
    if secs < 0:
        return None
    return round(secs / 3600.0, 4)


def resolve_session_billing(session: OperationSession, db: Session) -> SessionBilling:
    """
    Billing from OperationCharge for the tractor owner:
    - Most operations: total = charge_per_ha × area_ha
    - Threshing & Grading: total = charge_per_hour × session duration (hours)
    ``charge_per_ha_applied`` stores the applied rate (Rs/ha or Rs/hr) for API compatibility.
    """
    tractor = db.get(Tractor, session.tractor_id)
    owner_id = session.tractor_owner_id
    if owner_id is None and tractor is not None:
        owner_id = tractor.owner_id
    if owner_id is None:
        logger.warning("No owner on tractor - skipping cost")
        return SessionBilling(None, None, None)

    op_key = (session.operation_type or "").strip().lower()
    charge = db.scalars(
        select(OperationCharge).where(
            OperationCharge.owner_id == owner_id,
            func.lower(func.trim(OperationCharge.operation_type)) == op_key,
        )
    ).first()

    if charge is None:
        logger.warning(
            "No operation charge found for owner_id=%s operation_type=%r",
            owner_id,
            session.operation_type,
        )
        return SessionBilling(None, None, None)

    op_label = (session.operation_type or "").strip() or (session.operation_type or "Operation")

    if op_key in PER_HOUR_OPERATION_TYPES:
        hourly = charge.charge_per_hour
        if hourly is None or float(hourly) <= 0:
            logger.warning("No hourly rate for owner_id=%s operation_type=%r", owner_id, session.operation_type)
            return SessionBilling(None, None, "Hourly rate not configured for this operation")

        rate = float(hourly)
        hours = _session_duration_hours(session)
        if hours is None:
            return SessionBilling(None, rate, "Session duration unavailable - cost pending")

        total_cost = round(rate * hours, 2)
        note = f"{op_label}: Rs {rate}/hr × {hours} h = Rs {total_cost}"
        return SessionBilling(total_cost, rate, note)

    rate = float(charge.charge_per_ha)

    if session.area_ha is None:
        return SessionBilling(
            None,
            rate,
            "Area not computed - cost pending",
        )

    area = float(session.area_ha)
    total_cost = round(rate * area, 2)
    note = f"{op_label}: Rs {rate}/ha × {round(area, 4)} ha = Rs {total_cost}"
    return SessionBilling(total_cost, rate, note)


def compute_session_cost(session: OperationSession, db: Session) -> None:
    """
    Looks up the OperationCharge for this session's owner + operation_type.
    For area-based ops, uses session.area_ha (after finalize_session_area).
    For Threshing/Grading, uses session started/ended timestamps.
    Sets session.total_cost_inr, session.charge_per_ha_applied (applied rate), session.cost_note.
    Does NOT commit - caller commits.
    """
    b = resolve_session_billing(session, db)
    session.total_cost_inr = b.total_cost_inr
    session.charge_per_ha_applied = b.charge_per_ha_applied
    session.cost_note = b.cost_note


def session_billing_differs_from_persisted(session: OperationSession, billing: SessionBilling) -> bool:
    """True if DB session cost fields should be updated to match resolved billing."""
    pairs = [
        (session.total_cost_inr, billing.total_cost_inr),
        (session.charge_per_ha_applied, billing.charge_per_ha_applied),
    ]
    for stored, resolved in pairs:
        if stored is None and resolved is None:
            continue
        if stored is None or resolved is None:
            return True
        if abs(float(stored) - float(resolved)) > 0.005:
            return True
    return False

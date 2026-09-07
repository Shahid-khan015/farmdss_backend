"""Session billing: lock the rate at start, compute the charge once at end.

The pricing rules themselves are the owner's and are not defined here -- this module only
applies them:

* every operation is charged per hectare, except Threshing and Grading which are charged
  per hour (:mod:`app.core.billing_units` is the single definition of that split);
* the rate for each operation/unit is whatever the tractor's owner configured in
  ``operation_charges``.

What this module guarantees is *when* those inputs are read:

``lock_session_rate``
    Runs once, inside the ``POST /sessions/start`` transaction. It copies the owner's rate
    and its unit onto the session row. Nothing afterwards consults ``operation_charges``
    again, so an owner editing their rate card mid-session -- or years later -- cannot
    change what an existing session costs. If no rate is configured the session is not
    allowed to start at all, which is what makes "every session has a computable charge"
    an invariant rather than a hope.

``finalize_session_billing``
    Runs once, at stop/cancel, and is a no-op if ``cost_finalized_at`` is already set. It
    is the only writer of ``total_cost_inr``. Read paths report the stored value; they do
    not recompute it. (They used to: the summary endpoint re-resolved billing on every
    request and silently rewrote finished sessions whenever the current rate differed.)

Arithmetic is ``Decimal`` throughout. The billed quantity is quantized exactly once, and
the cost note prints that same quantized value, so ``rate x quantity = total`` can be
verified by hand from the summary alone. The previous code rounded hours to 4dp before
multiplying but printed a differently-rounded figure, so a note could state a
multiplication that did not produce its own total.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.billing_units import (
    CHARGE_UNIT_PER_HOUR,
    charge_unit_for,
    unit_suffix,
)
from app.models.operation_charge import OperationCharge
from app.models.session import OperationSession, SessionPause

logger = logging.getLogger(__name__)

#: Rupees are quantized to paise, half-up. ``round()`` is not used anywhere in this module:
#: it rounds half-to-even, so ``round(2.675, 2)`` disagrees with what a printed bill says.
MONEY_QUANTUM = Decimal("0.01")

#: Billable hours are fixed to microhours (3.6 ms). This is the *billed* quantity, not a
#: display rounding -- see ``finalize_session_billing``.
HOURS_QUANTUM = Decimal("0.000001")

_SECONDS_PER_HOUR = Decimal(3600)


class RateNotConfigured(Exception):
    """The owner has no usable rate for this operation, so the session cannot be priced.

    Raised from ``lock_session_rate`` and surfaced as a 422 by the start-session route:
    refusing the start is what keeps an unpriceable session from ever existing.
    """

    def __init__(self, operation_type: str, reason: str) -> None:
        super().__init__(reason)
        self.operation_type = operation_type
        self.reason = reason


def _to_decimal(value: object | None) -> Optional[Decimal]:
    """Coerce a DB/ORM numeric to Decimal without going through binary float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Rate lock (session start)
# ---------------------------------------------------------------------------

def resolve_operation_charge(
    *,
    db: Session,
    owner_id: uuid.UUID | None,
    operation_type: str,
) -> OperationCharge | None:
    """The owner's rate-card row for this operation, or None.

    Matched case- and whitespace-insensitively on ``operation_type``, and strictly on
    ``owner_id``. There is deliberately no "if only one owner has configured this
    operation, use theirs" fallback: that used to let a session on an owner-less tractor
    be billed at an unrelated owner's rate.
    """
    if owner_id is None:
        return None
    op_key = (operation_type or "").strip().lower()
    return db.scalars(
        select(OperationCharge).where(
            OperationCharge.owner_id == owner_id,
            func.lower(func.trim(OperationCharge.operation_type)) == op_key,
        )
    ).first()


def lock_session_rate(
    session: OperationSession,
    *,
    owner_id: uuid.UUID | None,
    db: Session,
) -> None:
    """Snapshot the owner's rate and unit onto ``session``. Raises RateNotConfigured.

    Call exactly once, before the session row is committed. Does not commit.
    """
    operation_type = session.operation_type or ""
    unit = charge_unit_for(operation_type)

    if owner_id is None:
        raise RateNotConfigured(
            operation_type,
            "This tractor has no owner on record, so no rate can be applied.",
        )

    charge = resolve_operation_charge(db=db, owner_id=owner_id, operation_type=operation_type)
    if charge is None:
        raise RateNotConfigured(
            operation_type,
            f"No rate configured for {operation_type}. Ask the tractor owner to set it.",
        )

    raw_rate = charge.charge_per_hour if unit == CHARGE_UNIT_PER_HOUR else charge.charge_per_ha
    rate = _to_decimal(raw_rate)
    if rate is None or rate <= 0:
        per = "per hour" if unit == CHARGE_UNIT_PER_HOUR else "per hectare"
        raise RateNotConfigured(
            operation_type,
            f"No {per} rate configured for {operation_type}. Ask the tractor owner to set it.",
        )

    session.charge_unit = unit
    session.charge_per_ha_applied = rate
    session.rate_currency = charge.currency or "INR"
    session.operation_charge_id = charge.id


# ---------------------------------------------------------------------------
# Measured quantities
# ---------------------------------------------------------------------------

def paused_seconds(session_id: uuid.UUID, db: Session, *, until: datetime | None = None) -> Decimal:
    """Total paused duration for a session, in seconds.

    An interval still open (``resumed_at IS NULL``) is measured up to ``until`` -- the
    session's ``ended_at`` for a finished session, otherwise now -- so a session stopped
    while paused does not bill the trailing pause.
    """
    boundary = _to_utc(until) if until is not None else datetime.now(timezone.utc)
    rows = db.scalars(
        select(SessionPause).where(SessionPause.session_id == session_id)
    ).all()

    total = Decimal(0)
    for row in rows:
        start = _to_utc(row.paused_at)
        end = _to_utc(row.resumed_at) if row.resumed_at is not None else boundary
        span = (end - start).total_seconds()
        if span > 0:
            total += Decimal(str(span))
    return total


def billable_hours(session: OperationSession, db: Session) -> Optional[Decimal]:
    """Worked hours: wall clock from start to end, minus every paused interval.

    None when the session has not ended (there is no final duration yet) or the clock ran
    backwards. Never negative.
    """
    if session.started_at is None or session.ended_at is None:
        return None

    elapsed = (_to_utc(session.ended_at) - _to_utc(session.started_at)).total_seconds()
    if elapsed < 0:
        return None

    worked = Decimal(str(elapsed)) - paused_seconds(session.id, db, until=session.ended_at)
    if worked < 0:
        worked = Decimal(0)
    return worked / _SECONDS_PER_HOUR


def worked_area_ha(session: OperationSession) -> Optional[Decimal]:
    """Hectares actually covered, as finalized by ``field_area_service``."""
    return _to_decimal(session.area_ha)


# ---------------------------------------------------------------------------
# Finalization (session end)
# ---------------------------------------------------------------------------

def _format_rate(rate: Decimal) -> str:
    """Trim trailing zeros so a note reads 'Rs 1200/ha', not 'Rs 1200.0000/ha'."""
    trimmed = rate.normalize()
    if trimmed == trimmed.to_integral_value():
        trimmed = trimmed.to_integral_value()
    return f"{trimmed:f}"


def finalize_session_billing(session: OperationSession, db: Session) -> None:
    """Compute and persist the final charge. Idempotent. Does NOT commit.

    No-op once ``cost_finalized_at`` is set -- that is the guarantee that a session's
    charge cannot move after it is issued.
    """
    if session.cost_finalized_at is not None:
        return

    rate = _to_decimal(session.charge_per_ha_applied)
    if rate is None:
        # Only reachable for sessions created before rates were locked at start.
        logger.warning(
            "Session %s has no locked rate; leaving cost pending", session.id
        )
        session.cost_note = "No rate was locked for this session - cost cannot be computed"
        return

    op_label = (session.operation_type or "Operation").strip() or "Operation"
    suffix = unit_suffix(session.charge_unit)

    if session.charge_unit == CHARGE_UNIT_PER_HOUR:
        raw_hours = billable_hours(session, db)
        if raw_hours is None:
            session.cost_note = "Session duration unavailable - cost pending"
            return
        # Quantize ONCE, here, and bill exactly this value. Elapsed seconds divided by
        # 3600 is a non-terminating decimal, so something has to be truncated; doing it at
        # the point the quantity is fixed -- rather than only when the note is formatted --
        # is what keeps `rate x quantity = total` checkable by hand from the summary.
        # HOURS_QUANTUM is fine enough that the discarded remainder cannot move a 2dp
        # rupee total for any rate below Rs 1,000,000/hr.
        quantity = raw_hours.quantize(HOURS_QUANTUM, rounding=ROUND_HALF_UP)
        session.billable_hours = float(quantity)
        quantity_text = f"{quantity:f}"
    else:
        # `area_ha` is already stored quantized to 4dp by `finalize_session_area`, so the
        # measured quantity and the billed quantity are the same number by construction.
        quantity = worked_area_ha(session)
        if quantity is None:
            session.cost_note = "Area not computed - cost pending"
            return
        quantity_text = f"{quantity:.4f}"

    total = (rate * quantity).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)

    note = (
        f"{op_label}: Rs {_format_rate(rate)}/{suffix} x {quantity_text} {suffix} "
        f"= Rs {total}"
    )
    if quantity == 0:
        note += " (no measured work recorded for this session)"

    session.total_cost_inr = total
    session.cost_note = note
    session.cost_finalized_at = datetime.now(timezone.utc)


def finalize_cancelled_session(session: OperationSession, db: Session) -> None:
    """Close a cancelled session at zero. Idempotent. Does NOT commit."""
    if session.cost_finalized_at is not None:
        return
    session.total_cost_inr = Decimal("0.00")
    session.cost_note = "Session cancelled - no charge"
    session.cost_finalized_at = datetime.now(timezone.utc)


def close_open_pause(session: OperationSession, db: Session, *, at: datetime) -> None:
    """Close the currently-open pause interval, if any. Does NOT commit.

    Called on resume, stop and cancel. Tolerates there being no open interval -- a paused
    session with no open row is a data anomaly, not a reason to fail the operator's
    request.
    """
    open_pause = db.scalars(
        select(SessionPause)
        .where(SessionPause.session_id == session.id, SessionPause.resumed_at.is_(None))
        .order_by(SessionPause.paused_at.desc())
    ).first()
    if open_pause is None:
        if session.status == "paused":
            logger.warning("Session %s is paused but has no open pause interval", session.id)
        return
    open_pause.resumed_at = at

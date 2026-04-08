from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.middleware.auth import get_current_user, require_role
from app.models.session import FuelLog, OperationSession, WageRecord
from app.models.tractor import Tractor
from app.models.user import User
from app.schemas.session import FuelLogResponse, WageRecordResponse
from app.services.wage_service import compute_wage

router = APIRouter(prefix="/api/v1", tags=["Wages"])


class FuelLogCreate(BaseModel):
    tractor_id: str
    session_id: Optional[str] = None
    litres: float = Field(gt=0)
    refilled_at: datetime
    cost_per_litre: Optional[float] = None
    notes: Optional[str] = None


class DisputeBody(BaseModel):
    reason: str


def _owner_tractor_ids_select(owner_id: uuid.UUID):
    return (
        select(OperationSession.tractor_id)
        .join(Tractor, OperationSession.tractor_id == Tractor.id)
        .where(
            or_(
                OperationSession.tractor_owner_id == owner_id,
                Tractor.owner_id == owner_id,
            )
        )
        .distinct()
    )


def _owner_uses_tractor(db: Session, owner_id: uuid.UUID, tractor_id: uuid.UUID) -> bool:
    tractor = db.get(Tractor, tractor_id)
    if tractor is not None and tractor.owner_id == owner_id:
        return True
    row = db.scalars(
        select(OperationSession.id).where(
            OperationSession.tractor_owner_id == owner_id,
            OperationSession.tractor_id == tractor_id,
        ).limit(1),
    ).first()
    return row is not None


@router.post("/wages/compute/{session_id}", response_model=WageRecordResponse)
def compute_wage_for_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["owner"])),
):
    sess = db.get(OperationSession, session_id)
    if sess is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if sess.tractor_owner_id != current_user.id:
        tractor = db.get(Tractor, sess.tractor_id)
        if tractor is None or tractor.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only compute wages for sessions on your tractors",
            )
    try:
        record = compute_wage(session_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(record)
    return WageRecordResponse.model_validate(record)


@router.get("/wages/", response_model=List[WageRecordResponse])
def list_wages(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["owner"])),
    operator_id: Optional[str] = Query(default=None),
    approved: Optional[bool] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = (
        select(WageRecord)
        .join(OperationSession, WageRecord.session_id == OperationSession.id)
        .join(Tractor, OperationSession.tractor_id == Tractor.id)
        .where(
            or_(
                OperationSession.tractor_owner_id == current_user.id,
                Tractor.owner_id == current_user.id,
            )
        )
    )
    if operator_id is not None:
        stmt = stmt.where(WageRecord.operator_id == uuid.UUID(operator_id))
    if approved is not None:
        stmt = stmt.where(WageRecord.approved == approved)
    stmt = stmt.order_by(WageRecord.created_at.desc()).offset(offset).limit(limit)
    rows = list(db.scalars(stmt).all())
    return [WageRecordResponse.model_validate(r) for r in rows]


def _can_view_wage(wage: WageRecord, user: User) -> bool:
    if user.role == "owner":
        return True
    return wage.operator_id == user.id


@router.get("/wages/{wage_id}", response_model=WageRecordResponse)
def get_wage(
    wage_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    wage = db.get(WageRecord, wage_id)
    if wage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wage record not found")
    if not _can_view_wage(wage, current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return WageRecordResponse.model_validate(wage)


def _wage_belongs_to_owner_session(db: Session, wage: WageRecord, owner_id: uuid.UUID) -> bool:
    sess = db.get(OperationSession, wage.session_id)
    if sess is None:
        return False
    if sess.tractor_owner_id == owner_id:
        return True
    tractor = db.get(Tractor, sess.tractor_id)
    return tractor is not None and tractor.owner_id == owner_id


@router.patch("/wages/{wage_id}/approve", response_model=WageRecordResponse)
def approve_wage(
    wage_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["owner"])),
):
    wage = db.get(WageRecord, wage_id)
    if wage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wage record not found")
    if not _wage_belongs_to_owner_session(db, wage, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    wage.approved = True
    wage.approved_by = current_user.id
    wage.approved_at = datetime.now(timezone.utc)
    db.add(wage)
    db.commit()
    db.refresh(wage)
    return WageRecordResponse.model_validate(wage)


@router.patch("/wages/{wage_id}/dispute", response_model=WageRecordResponse)
def dispute_wage(
    wage_id: uuid.UUID,
    body: DisputeBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["operator"])),
):
    wage = db.get(WageRecord, wage_id)
    if wage is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wage record not found")
    if wage.operator_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    wage.disputed = True
    wage.dispute_reason = body.reason
    db.add(wage)
    db.commit()
    db.refresh(wage)
    return WageRecordResponse.model_validate(wage)


@router.post("/fuel-logs/", response_model=FuelLogResponse)
def create_fuel_log(
    payload: FuelLogCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["operator", "owner"])),
):
    tractor_uid = uuid.UUID(payload.tractor_id)
    session_uid = uuid.UUID(payload.session_id) if payload.session_id else None

    tractor = db.get(Tractor, tractor_uid)
    if tractor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")

    if current_user.role == "owner":
        if not _owner_uses_tractor(db, current_user.id, tractor_uid):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tractor is not associated with your sessions",
            )

    op_session: Optional[OperationSession] = None
    if session_uid is not None:
        op_session = db.get(OperationSession, session_uid)
        if op_session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if op_session.tractor_id != tractor_uid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session does not match tractor",
            )
        if current_user.role == "operator" and op_session.operator_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only log fuel for your own sessions",
            )
        if current_user.role == "owner" and op_session.tractor_owner_id != current_user.id:
            t = db.get(Tractor, op_session.tractor_id)
            if t is None or t.owner_id != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Session is not on your tractors",
                )

    total_cost = None
    if payload.cost_per_litre is not None:
        total_cost = round(payload.litres * payload.cost_per_litre, 2)

    log = FuelLog(
        tractor_id=tractor_uid,
        session_id=session_uid,
        litres=payload.litres,
        refilled_at=payload.refilled_at,
        cost_per_litre=payload.cost_per_litre,
        total_cost=total_cost,
        entered_by=current_user.id,
        notes=payload.notes,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return FuelLogResponse.model_validate(log)


@router.get("/fuel-logs/", response_model=List[FuelLogResponse])
def list_fuel_logs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["owner", "operator"])),
    tractor_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
):
    stmt = select(FuelLog)

    if current_user.role == "owner":
        stmt = stmt.where(FuelLog.tractor_id.in_(_owner_tractor_ids_select(current_user.id)))
    else:
        stmt = stmt.where(FuelLog.entered_by == current_user.id)

    if tractor_id is not None:
        stmt = stmt.where(FuelLog.tractor_id == uuid.UUID(tractor_id))
    if session_id is not None:
        stmt = stmt.where(FuelLog.session_id == uuid.UUID(session_id))

    if start_date is not None:
        start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        stmt = stmt.where(FuelLog.refilled_at >= start_dt)
    if end_date is not None:
        end_dt = datetime.combine(end_date, time(23, 59, 59, 999999)).replace(tzinfo=timezone.utc)
        stmt = stmt.where(FuelLog.refilled_at <= end_dt)

    stmt = stmt.order_by(FuelLog.refilled_at.desc()).limit(limit)
    rows = list(db.scalars(stmt).all())
    return [FuelLogResponse.model_validate(r) for r in rows]

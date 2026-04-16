from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.middleware.auth import get_current_user, require_role
from app.models.operation_charge import OperationCharge
from app.models.user import User
from app.schemas.operation_charge import (
    PER_HOUR_OPERATION_TYPES,
    OperationChargeCreate,
    OperationChargeRead,
    OperationChargeUpdate,
)

router = APIRouter(prefix="/api/v1/operation-charges", tags=["Operation Charges"])


def _to_read(charge: OperationCharge) -> OperationChargeRead:
    return OperationChargeRead(
        id=str(charge.id),
        owner_id=str(charge.owner_id),
        operation_type=charge.operation_type,
        charge_per_ha=float(charge.charge_per_ha),
        charge_per_hour=float(charge.charge_per_hour) if charge.charge_per_hour is not None else None,
        currency=charge.currency,
        created_at=charge.created_at,
        updated_at=charge.updated_at,
    )


def _normalize_charge_row(row: OperationCharge) -> None:
    op = (row.operation_type or "").strip().lower()
    if op in PER_HOUR_OPERATION_TYPES:
        if row.charge_per_hour is None or float(row.charge_per_hour) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Threshing and Grading require a positive charge per hour.",
            )
        row.charge_per_ha = 0.0
    else:
        if float(row.charge_per_ha) <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Charge per hectare must be positive.",
            )
        row.charge_per_hour = None


@router.post("", response_model=OperationChargeRead)
def upsert_operation_charge(
    body: OperationChargeCreate,
    current_user: User = Depends(require_role(["owner"])),
    db: Session = Depends(get_db),
):
    row = db.scalars(
        select(OperationCharge).where(
            OperationCharge.owner_id == current_user.id,
            OperationCharge.operation_type == body.operation_type,
        )
    ).first()
    if row is None:
        row = OperationCharge(
            owner_id=current_user.id,
            operation_type=body.operation_type,
            charge_per_ha=body.charge_per_ha,
            charge_per_hour=body.charge_per_hour,
        )
        db.add(row)
    else:
        row.charge_per_ha = body.charge_per_ha
        row.charge_per_hour = body.charge_per_hour
        db.add(row)
    _normalize_charge_row(row)
    db.commit()
    db.refresh(row)
    return _to_read(row)


@router.get("", response_model=list[OperationChargeRead])
def list_operation_charges(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(OperationCharge)
    if current_user.role == "owner":
        stmt = stmt.where(OperationCharge.owner_id == current_user.id)
    rows = list(db.scalars(stmt.order_by(OperationCharge.operation_type.asc())).all())
    return [_to_read(row) for row in rows]


@router.patch("/{charge_id}", response_model=OperationChargeRead)
def update_operation_charge(
    charge_id: uuid.UUID,
    body: OperationChargeUpdate,
    current_user: User = Depends(require_role(["owner"])),
    db: Session = Depends(get_db),
):
    row = db.get(OperationCharge, charge_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation charge not found")
    if row.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if body.charge_per_ha is not None:
        row.charge_per_ha = body.charge_per_ha
    if body.charge_per_hour is not None:
        row.charge_per_hour = body.charge_per_hour
    db.add(row)
    _normalize_charge_row(row)
    db.commit()
    db.refresh(row)
    return _to_read(row)


@router.delete("/{charge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_operation_charge(
    charge_id: uuid.UUID,
    current_user: User = Depends(require_role(["owner"])),
    db: Session = Depends(get_db),
):
    row = db.get(OperationCharge, charge_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation charge not found")
    if row.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

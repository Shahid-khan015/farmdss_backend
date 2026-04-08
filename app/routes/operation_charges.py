from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.middleware.auth import get_current_user, require_role
from app.models.operation_charge import OperationCharge
from app.models.user import User
from app.schemas.operation_charge import OperationChargeCreate, OperationChargeRead, OperationChargeUpdate

router = APIRouter(prefix="/api/v1/operation-charges", tags=["Operation Charges"])


def _to_read(charge: OperationCharge) -> OperationChargeRead:
    return OperationChargeRead(
        id=str(charge.id),
        owner_id=str(charge.owner_id),
        operation_type=charge.operation_type,
        charge_per_ha=charge.charge_per_ha,
        currency=charge.currency,
        created_at=charge.created_at,
        updated_at=charge.updated_at,
    )


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
        )
        db.add(row)
    else:
        row.charge_per_ha = body.charge_per_ha
        db.add(row)
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
    db.add(row)
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

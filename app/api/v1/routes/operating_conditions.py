from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud.operating_condition import operating_condition_crud
from app.schemas.common import DeleteResponse, PaginatedResponse
from app.schemas.operating_condition import (
    OperatingConditionCreate,
    OperatingConditionRead,
    OperatingConditionUpdate,
)

router = APIRouter()


@router.get("", response_model=PaginatedResponse[OperatingConditionRead])
def list_operating_conditions(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = operating_condition_crud.list(db, q=q, limit=limit, offset=offset)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/{id}", response_model=OperatingConditionRead)
def get_operating_condition(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = operating_condition_crud.get(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return obj


@router.post("", response_model=OperatingConditionRead, status_code=status.HTTP_201_CREATED)
def create_operating_condition(payload: OperatingConditionCreate, db: Session = Depends(get_db)):
    return operating_condition_crud.create(db, obj_in=payload)


@router.put("/{id}", response_model=OperatingConditionRead)
def update_operating_condition(id: uuid.UUID, payload: OperatingConditionUpdate, db: Session = Depends(get_db)):
    obj = operating_condition_crud.get(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return operating_condition_crud.update(db, db_obj=obj, obj_in=payload)


@router.delete("/{id}", response_model=DeleteResponse)
def delete_operating_condition(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = operating_condition_crud.remove(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return {"ok": True, "id": id}


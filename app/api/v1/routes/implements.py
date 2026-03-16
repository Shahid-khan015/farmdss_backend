from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud.implement import implement_crud
from app.crud.simulation import simulation_crud
from app.schemas.common import DeleteResponse, PaginatedResponse
from app.schemas.implement import ImplementCreate, ImplementRead, ImplementUpdate
from app.schemas.simulation import SimulationRead

router = APIRouter()


@router.get("", response_model=PaginatedResponse[ImplementRead])
def list_implements(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None),
    implement_type: Optional[str] = Query(default=None),
    manufacturer: Optional[str] = Query(default=None),
    is_library: Optional[bool] = Query(default=None),
    sort: str = Query(default="name", pattern="^(name|weight)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = implement_crud.list(
        db,
        q=q,
        implement_type=implement_type,
        manufacturer=manufacturer,
        is_library=is_library,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/{id}", response_model=ImplementRead)
def get_implement(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = implement_crud.get(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Implement not found")
    return obj


@router.post("", response_model=ImplementRead, status_code=status.HTTP_201_CREATED)
def create_implement(payload: ImplementCreate, db: Session = Depends(get_db)):
    return implement_crud.create(db, obj_in=payload)


@router.put("/{id}", response_model=ImplementRead)
def update_implement(id: uuid.UUID, payload: ImplementUpdate, db: Session = Depends(get_db)):
    obj = implement_crud.get(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Implement not found")
    return implement_crud.update(db, db_obj=obj, obj_in=payload)


@router.delete("/{id}", response_model=DeleteResponse)
def delete_implement(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = implement_crud.remove(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Implement not found")
    return {"ok": True, "id": id}


@router.get("/{id}/simulations", response_model=PaginatedResponse[SimulationRead])
def list_implement_simulations(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = simulation_crud.list_by_implement(db, implement_id=id, limit=limit, offset=offset)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


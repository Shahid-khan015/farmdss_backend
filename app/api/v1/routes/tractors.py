from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud.simulation import simulation_crud
from app.crud.tractor import tractor_crud
from app.crud.tire_specification import tire_crud
from app.schemas.common import DeleteResponse, PaginatedResponse
from app.schemas.simulation import SimulationRead
from app.schemas.tractor import TractorCreate, TractorRead, TractorUpdate

router = APIRouter()


@router.get("", response_model=PaginatedResponse[TractorRead])
def list_tractors(
    db: Session = Depends(get_db),
    q: Optional[str] = Query(default=None),
    manufacturer: Optional[str] = Query(default=None),
    drive_mode: Optional[str] = Query(default=None),
    is_library: Optional[bool] = Query(default=None),
    sort: str = Query(default="name", pattern="^(name|power)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = tractor_crud.list(
        db,
        q=q,
        manufacturer=manufacturer,
        drive_mode=drive_mode,
        is_library=is_library,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "items": items, "limit": limit, "offset": offset}


@router.get("/{id}", response_model=TractorRead)
def get_tractor(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = tractor_crud.get_with_tires(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    return obj


@router.post("", response_model=TractorRead, status_code=status.HTTP_201_CREATED)
def create_tractor(payload: TractorCreate, db: Session = Depends(get_db)):
    tire_payload = payload.tire_specification
    tractor_data = payload.model_dump(exclude={"tire_specification"})
    tractor = tractor_crud.create(db, obj_in=TractorCreate(**tractor_data))
    if tire_payload is not None:
        tire_crud.create(db, obj_in=tire_payload, extra={"tractor_id": tractor.id})
    return tractor_crud.get_with_tires(db, id=tractor.id)


@router.put("/{id}", response_model=TractorRead)
def update_tractor(id: uuid.UUID, payload: TractorUpdate, db: Session = Depends(get_db)):
    tractor = tractor_crud.get(db, id=id)
    if not tractor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    tractor_crud.update(db, db_obj=tractor, obj_in=payload)
    return tractor_crud.get_with_tires(db, id=id)


@router.delete("/{id}", response_model=DeleteResponse)
def delete_tractor(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = tractor_crud.remove(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    return {"ok": True, "id": id}


@router.get("/{id}/simulations", response_model=PaginatedResponse[SimulationRead])
def list_tractor_simulations(
    id: uuid.UUID,
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    total, items = simulation_crud.list_by_tractor(db, tractor_id=id, limit=limit, offset=offset)
    return {"total": total, "items": items, "limit": limit, "offset": offset}


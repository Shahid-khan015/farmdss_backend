from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud.tire_specification import tire_crud
from app.crud.tractor import tractor_crud
from app.schemas.common import DeleteResponse
from app.schemas.tire_specification import (
    TireSpecificationCreate,
    TireSpecificationRead,
    TireSpecificationUpdate,
)

router = APIRouter()


@router.get("/tractors/{tractor_id}/tires", response_model=TireSpecificationRead)
def get_tires_for_tractor(tractor_id: uuid.UUID, db: Session = Depends(get_db)):
    tractor = tractor_crud.get(db, id=tractor_id)
    if not tractor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    tires = tire_crud.get_by_tractor_id(db, tractor_id=tractor_id)
    if not tires:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tire specification not found"
        )
    return tires


@router.post("/tractors/{tractor_id}/tires", response_model=TireSpecificationRead)
def add_or_update_tires_for_tractor(
    tractor_id: uuid.UUID, payload: TireSpecificationCreate, db: Session = Depends(get_db)
):
    tractor = tractor_crud.get(db, id=tractor_id)
    if not tractor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")
    existing = tire_crud.get_by_tractor_id(db, tractor_id=tractor_id)
    if existing:
        return tire_crud.update(db, db_obj=existing, obj_in=payload.model_dump(exclude_unset=True))
    return tire_crud.create(db, obj_in=payload, extra={"tractor_id": tractor_id})


@router.put("/tires/{id}", response_model=TireSpecificationRead)
def update_tires(id: uuid.UUID, payload: TireSpecificationUpdate, db: Session = Depends(get_db)):
    tires = tire_crud.get(db, id=id)
    if not tires:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tire specification not found")
    return tire_crud.update(db, db_obj=tires, obj_in=payload)


@router.delete("/tires/{id}", response_model=DeleteResponse)
def delete_tires(id: uuid.UUID, db: Session = Depends(get_db)):
    obj = tire_crud.remove(db, id=id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tire specification not found")
    return {"ok": True, "id": id}


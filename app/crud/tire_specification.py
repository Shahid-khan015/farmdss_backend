from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.tire_specification import TireSpecification
from app.schemas.tire_specification import TireSpecificationCreate, TireSpecificationUpdate


class CRUDTireSpecification(CRUDBase[TireSpecification, TireSpecificationCreate, TireSpecificationUpdate]):
    def get_by_tractor_id(self, db: Session, *, tractor_id: uuid.UUID) -> Optional[TireSpecification]:
        stmt = select(TireSpecification).where(TireSpecification.tractor_id == tractor_id)
        return db.scalars(stmt).first()


tire_crud = CRUDTireSpecification(TireSpecification)


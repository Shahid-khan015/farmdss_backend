from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.crud.base import CRUDBase
from app.models.tractor import Tractor
from app.schemas.tractor import TractorCreate, TractorUpdate


class CRUDTractor(CRUDBase[Tractor, TractorCreate, TractorUpdate]):
    def get_with_tires(self, db: Session, *, id: uuid.UUID) -> Optional[Tractor]:
        stmt = (
            select(Tractor)
            .where(Tractor.id == id)
            .options(selectinload(Tractor.tire_specification))
        )
        return db.scalars(stmt).first()

    def list(
        self,
        db: Session,
        *,
        q: Optional[str] = None,
        manufacturer: Optional[str] = None,
        drive_mode: Optional[str] = None,
        is_library: Optional[bool] = None,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Tractor]]:
        stmt = select(Tractor).options(selectinload(Tractor.tire_specification))

        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Tractor.name.ilike(like), Tractor.model.ilike(like)))
        if manufacturer:
            stmt = stmt.where(Tractor.manufacturer == manufacturer)
        if drive_mode:
            stmt = stmt.where(Tractor.drive_mode == drive_mode)
        if is_library is not None:
            stmt = stmt.where(Tractor.is_library == is_library)

        if sort == "power":
            stmt = stmt.order_by(Tractor.pto_power.desc().nullslast(), Tractor.name.asc())
        else:
            stmt = stmt.order_by(Tractor.name.asc())

        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)


tractor_crud = CRUDTractor(Tractor)


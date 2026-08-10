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
        current_user_id: Optional[uuid.UUID] = None,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Tractor]]:
        """List tractors.

        Library tractors (is_library=True) are shared reference data visible to
        everyone. Custom tractors are private to their owner: when
        `current_user_id` is given, any non-library row is only returned if it
        belongs to that user -- otherwise every user's custom copies would leak
        into every other user's "My Tractors" list (this previously showed up
        as apparent "duplicate" library entries).
        """
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
        if current_user_id is not None:
            stmt = stmt.where(or_(Tractor.is_library == True, Tractor.owner_id == current_user_id))  # noqa: E712

        if sort == "power":
            stmt = stmt.order_by(Tractor.pto_power.desc().nullslast(), Tractor.name.asc())
        else:
            stmt = stmt.order_by(Tractor.name.asc())

        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)


tractor_crud = CRUDTractor(Tractor)


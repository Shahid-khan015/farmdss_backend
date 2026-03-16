from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.implement import Implement
from app.schemas.implement import ImplementCreate, ImplementUpdate


class CRUDImplement(CRUDBase[Implement, ImplementCreate, ImplementUpdate]):
    def list(
        self,
        db: Session,
        *,
        q: Optional[str] = None,
        implement_type: Optional[str] = None,
        manufacturer: Optional[str] = None,
        is_library: Optional[bool] = None,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Implement]]:
        stmt = select(Implement)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Implement.name.ilike(like), Implement.manufacturer.ilike(like)))
        if implement_type:
            stmt = stmt.where(Implement.implement_type == implement_type)
        if manufacturer:
            stmt = stmt.where(Implement.manufacturer == manufacturer)
        if is_library is not None:
            stmt = stmt.where(Implement.is_library == is_library)

        if sort == "weight":
            stmt = stmt.order_by(Implement.weight.desc().nullslast(), Implement.name.asc())
        else:
            stmt = stmt.order_by(Implement.name.asc())

        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)


implement_crud = CRUDImplement(Implement)


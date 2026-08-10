from __future__ import annotations

import uuid
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
        current_user_id: Optional[uuid.UUID] = None,
        sort: str = "name",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Implement]]:
        """List implements.

        Library implements (is_library=True) are shared reference data visible
        to everyone. Custom implements are private to their owner: when
        `current_user_id` is given, any non-library row is only returned if it
        belongs to that user -- otherwise every user's custom copies would leak
        into every other user's "My Implements" list (this previously showed up
        as apparent "duplicate" library entries).
        """
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
        if current_user_id is not None:
            stmt = stmt.where(or_(Implement.is_library == True, Implement.owner_id == current_user_id))  # noqa: E712

        if sort == "weight":
            stmt = stmt.order_by(Implement.weight.desc().nullslast(), Implement.name.asc())
        else:
            stmt = stmt.order_by(Implement.name.asc())

        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)


implement_crud = CRUDImplement(Implement)


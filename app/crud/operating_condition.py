from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.operating_condition import OperatingConditionPreset
from app.schemas.operating_condition import OperatingConditionCreate, OperatingConditionUpdate


class CRUDOperatingCondition(
    CRUDBase[OperatingConditionPreset, OperatingConditionCreate, OperatingConditionUpdate]
):
    def list(self, db: Session, *, q: Optional[str] = None, limit: int = 50, offset: int = 0):
        stmt = select(OperatingConditionPreset)
        if q:
            stmt = stmt.where(OperatingConditionPreset.name.ilike(f"%{q.strip()}%"))
        stmt = stmt.order_by(OperatingConditionPreset.name.asc())
        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)


operating_condition_crud = CRUDOperatingCondition(OperatingConditionPreset)


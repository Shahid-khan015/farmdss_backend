from __future__ import annotations

import uuid
from typing import List

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.implement import Implement
from app.models.tractor import Tractor


def _implement_has_pto_rpm_required() -> bool:
    return hasattr(Implement, "pto_rpm_required")


def _tractor_compatibility_fields_all_null(tractor: Tractor) -> bool:
    return (
        tractor.pto_rpm_min is None
        and tractor.pto_rpm_max is None
        and tractor.tow_capacity_kg is None
        and tractor.hitch_type is None
    )


def get_compatible_implements(tractor_id: uuid.UUID, db: Session) -> List[Implement]:
    tractor = db.get(Tractor, tractor_id)
    if tractor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tractor not found")

    stmt = select(Implement)
    if tractor.owner_id is None:
        stmt = stmt.where(Implement.is_library.is_(True))
    else:
        stmt = stmt.where(
            or_(Implement.is_library.is_(True), Implement.owner_id == tractor.owner_id)
        )

    if _tractor_compatibility_fields_all_null(tractor):
        return list(db.scalars(stmt).all())

    if tractor.hitch_type is not None:
        stmt = stmt.where(
            or_(Implement.hitch_type.is_(None), Implement.hitch_type == tractor.hitch_type)
        )

    if tractor.pto_rpm_max is not None and _implement_has_pto_rpm_required():
        stmt = stmt.where(
            or_(
                Implement.pto_rpm_required.is_(None),
                Implement.pto_rpm_required <= tractor.pto_rpm_max,
            )
        )

    return list(db.scalars(stmt).all())

from __future__ import annotations

import uuid
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.database import Base

ModelType = TypeVar("ModelType", bound=Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    def __init__(self, model: type[ModelType]):
        self.model = model

    def get(self, db: Session, *, id: uuid.UUID) -> Optional[ModelType]:
        return db.get(self.model, id)

    def create(self, db: Session, *, obj_in: CreateSchemaType, extra: dict[str, Any] | None = None) -> ModelType:
        data = obj_in.model_dump(exclude_unset=True)
        if extra:
            data.update(extra)
        db_obj = self.model(**data)  # type: ignore[arg-type]
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def update(
        self,
        db: Session,
        *,
        db_obj: ModelType,
        obj_in: UpdateSchemaType | dict[str, Any],
    ) -> ModelType:
        data = obj_in if isinstance(obj_in, dict) else obj_in.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(db_obj, field, value)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def remove(self, db: Session, *, id: uuid.UUID) -> Optional[ModelType]:
        obj = self.get(db, id=id)
        if not obj:
            return None
        db.delete(obj)
        db.commit()
        return obj

    def list_paginated(self, db: Session, *, stmt: Select, limit: int, offset: int) -> tuple[int, list[ModelType]]:
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        items = db.scalars(stmt.limit(limit).offset(offset)).all()
        return int(total), list(items)


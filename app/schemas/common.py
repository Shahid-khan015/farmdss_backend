from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Timestamped(ORMModel):
    created_at: datetime
    updated_at: datetime


T = TypeVar("T")


class PaginatedResponse(ORMModel, Generic[T]):
    total: int
    items: list[T]
    limit: int
    offset: int


class UUIDResponse(ORMModel):
    id: uuid.UUID


class DeleteResponse(ORMModel):
    ok: bool = True
    id: Optional[uuid.UUID] = None


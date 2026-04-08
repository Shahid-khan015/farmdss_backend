from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class OperationChargeCreate(BaseModel):
    owner_id: Optional[str] = None
    operation_type: str
    charge_per_ha: float


class OperationChargeUpdate(BaseModel):
    charge_per_ha: Optional[float] = None


class OperationChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str
    operation_type: str
    charge_per_ha: float
    currency: str
    created_at: datetime
    updated_at: datetime

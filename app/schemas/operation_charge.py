from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, model_validator


PER_HOUR_OPERATION_TYPES = frozenset({"threshing", "grading"})


class OperationChargeCreate(BaseModel):
    owner_id: Optional[str] = None
    operation_type: str
    charge_per_ha: Optional[float] = None
    charge_per_hour: Optional[float] = None

    @model_validator(mode="after")
    def validate_rates(self) -> OperationChargeCreate:
        op = (self.operation_type or "").strip().lower()
        if op in PER_HOUR_OPERATION_TYPES:
            hr = self.charge_per_hour
            if hr is None or hr <= 0:
                raise ValueError("Threshing and Grading require a positive charge per hour (Rs/hr).")
            self.charge_per_ha = 0.0
        else:
            ha = self.charge_per_ha
            if ha is None or ha <= 0:
                raise ValueError("Enter a positive charge per hectare.")
            self.charge_per_hour = None
        return self


class OperationChargeUpdate(BaseModel):
    charge_per_ha: Optional[float] = None
    charge_per_hour: Optional[float] = None


class OperationChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner_id: str
    operation_type: str
    charge_per_ha: float
    charge_per_hour: Optional[float] = None
    currency: str
    created_at: datetime
    updated_at: datetime

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.simulation import Simulation
from app.schemas.simulation import SimulationRunRequest


class CRUDSimulation(CRUDBase[Simulation, SimulationRunRequest, SimulationRunRequest]):
    def list(
        self,
        db: Session,
        *,
        tractor_id: Optional[uuid.UUID] = None,
        implement_id: Optional[uuid.UUID] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[Simulation]]:
        stmt = select(Simulation)
        if tractor_id:
            stmt = stmt.where(Simulation.tractor_id == tractor_id)
        if implement_id:
            stmt = stmt.where(Simulation.implement_id == implement_id)
        stmt = stmt.order_by(Simulation.created_at.desc())
        return self.list_paginated(db, stmt=stmt, limit=limit, offset=offset)

    def list_by_tractor(self, db: Session, *, tractor_id: uuid.UUID, limit: int = 50, offset: int = 0):
        return self.list(db, tractor_id=tractor_id, limit=limit, offset=offset)

    def list_by_implement(self, db: Session, *, implement_id: uuid.UUID, limit: int = 50, offset: int = 0):
        return self.list(db, implement_id=implement_id, limit=limit, offset=offset)


simulation_crud = CRUDSimulation(Simulation)


from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes.implements import router as implements_router
from app.api.v1.routes.iot import router as iot_router
from app.api.v1.routes.operating_conditions import router as operating_conditions_router
from app.api.v1.routes.simulations import router as simulations_router
from app.api.v1.routes.tractors import router as tractors_router
from app.api.v1.routes.tires import router as tires_router

api_router = APIRouter()

api_router.include_router(tractors_router, prefix="/tractors", tags=["tractors"])
api_router.include_router(tires_router, tags=["tire-specifications"])
api_router.include_router(implements_router, prefix="/implements", tags=["implements"])
api_router.include_router(
    operating_conditions_router, prefix="/operating-conditions", tags=["operating-conditions"]
)
api_router.include_router(simulations_router, prefix="/simulations", tags=["simulations"])
api_router.include_router(iot_router, prefix="/iot", tags=["iot"])


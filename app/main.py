from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.config import settings
from app.database import Base, engine
from app import models  # noqa: F401
from app.database import SessionLocal
from app.utils.seed_library import seed_library_if_empty


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        debug=settings.DEBUG,
    )

    # Allow Expo/dev clients to call API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.DEBUG else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # Auto-create tables only for SQLite fallback (dev convenience).
    if str(engine.url).startswith("sqlite"):
        Base.metadata.create_all(bind=engine)

    @app.on_event("startup")
    def _seed_library_data():
        db = SessionLocal()
        try:
            seed_library_if_empty(db)
        finally:
            db.close()

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


app = create_app()


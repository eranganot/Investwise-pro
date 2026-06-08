"""InvestWise Pro - FastAPI application entrypoint."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    allocation, decision_feed, entities, health, intake, lag, learning, risk, safety, simulation, tax, whs, workflows,
)
from app.core.config import get_settings
from app.core.database import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investwise")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables from models. Production uses Alembic.
    if settings.auto_create_tables:
        from app import models  # noqa: F401  register all tables
        from app.models.base import Base

        logger.info("Ensuring database schema (auto_create_tables=True)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    if settings.environment == "production":
        if settings.auto_create_tables:
            logger.warning("auto_create_tables is ON in production - prefer Alembic migrations.")
        if not settings.api_key:
            logger.warning("API_KEY is not set in production - write endpoints are unauthenticated.")
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp
    app.include_router(health.router)
    app.include_router(decision_feed.router)
    app.include_router(tax.router)
    app.include_router(risk.router)
    app.include_router(lag.router)
    app.include_router(whs.router)
    app.include_router(simulation.router)
    app.include_router(safety.router)
    app.include_router(learning.router)
    app.include_router(intake.router)
    app.include_router(entities.router)
    app.include_router(allocation.router)
    app.include_router(workflows.router)

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/dashboard", StaticFiles(directory=str(static_dir), html=True), name="dashboard")

    @app.get("/")
    async def root() -> dict:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()

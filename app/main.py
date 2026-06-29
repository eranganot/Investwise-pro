"""InvestWise Pro - FastAPI application entrypoint."""
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    adversary as adversary_routes, allocation, assistant, auth, backtest, broker, commodities, decision_feed, entities, fees, gfinance, google_auth, health, intake, jobs, lag, learning, market, push, screener, strategy,
    observability, plan, recommendations, risk, safety, simulation, tax, war_room, whatif, whs, workflows,
)
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, engine
from app.core.logging_config import configure_logging
from app.core.metrics import LATENCY, REQUESTS
from app.core.request_context import set_request_id

configure_logging()
logger = logging.getLogger("investwise")
settings = get_settings()

if settings.sentry_dsn:
    try:
        import sentry_sdk
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)
        logger.info("Sentry initialized")
    except Exception:  # noqa: BLE001
        logger.warning("SENTRY_DSN set but sentry-sdk not installed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables from models. Production uses Alembic.
    if settings.auto_create_tables:
        from app import models  # noqa: F401  register all tables
        from app.models.base import Base

        logger.info("Ensuring database schema (auto_create_tables=True)...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            if engine.dialect.name == "postgresql":
                from sqlalchemy import text
                for ddl in (
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS target_roi_pct DOUBLE PRECISION",
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS target_roi_period VARCHAR(12) DEFAULT 'yearly'",
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS target_yield_pct DOUBLE PRECISION",
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS target_yield_period VARCHAR(12) DEFAULT 'yearly'",
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS preferred_depth INTEGER",
                    "ALTER TABLE plans ADD COLUMN IF NOT EXISTS strategy VARCHAR(40)",
                ):
                    try:
                        await conn.execute(text(ddl))
                    except Exception:  # noqa: BLE001
                        pass
    if settings.environment == "production":
        if settings.auto_create_tables:
            logger.warning("auto_create_tables is ON in production - prefer Alembic migrations.")
        if not settings.api_key:
            logger.warning("API_KEY is not set in production - write endpoints are unauthenticated.")
    if settings.enable_scheduler:
        from app.worker.scheduler import start_scheduler
        start_scheduler()
    yield
    if settings.enable_scheduler:
        from app.worker.scheduler import shutdown_scheduler
        shutdown_scheduler()
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
    async def _observe(request, call_next):
        rid = uuid.uuid4().hex[:16]
        set_request_id(rid)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        REQUESTS.labels(request.method, str(response.status_code)).inc()
        LATENCY.labels(request.method).observe(time.perf_counter() - start)
        return response

    @app.middleware("http")
    async def _security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.middleware("http")
    async def _audit(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            body = await request.body()
            role = "open"
            auth_h = request.headers.get("authorization")
            if auth_h and auth_h.startswith("Bearer "):
                try:
                    from app.core.auth import decode_token
                    role = decode_token(auth_h.split(" ", 1)[1]).get("role", "unknown")
                except Exception:
                    role = "invalid"
            from app.core.audit import audit
            ip = request.client.host if request.client else "?"
            audit(method=request.method, path=request.url.path, ip=ip, role=role, payload=body)
            try:
                from app.models.tables import AuditLog
                async with AsyncSessionLocal() as s:
                    s.add(AuditLog(method=request.method, route=request.url.path, origin_ip=ip,
                                   role=role, payload_sha256=hashlib.sha256(body or b"").hexdigest()))
                    await s.commit()
            except Exception:  # noqa: BLE001 - audit must never break a request
                logger.warning("audit persistence failed", exc_info=False)
        return await call_next(request)
    app.include_router(health.router)
    app.include_router(observability.router)
    app.include_router(plan.router)
    app.include_router(recommendations.router)
    app.include_router(war_room.router)
    app.include_router(auth.router)
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
    app.include_router(market.router)
    app.include_router(jobs.router)
    app.include_router(whatif.router)
    app.include_router(broker.router)
    app.include_router(fees.router)
    app.include_router(backtest.router)
    app.include_router(adversary_routes.router)
    app.include_router(google_auth.router)
    app.include_router(assistant.router)
    app.include_router(strategy.router)
    app.include_router(commodities.router)
    app.include_router(screener.router)
    app.include_router(push.router)
    app.include_router(gfinance.router)

    # Legacy /dashboard consolidated into /app (Phase G): keep a redirect for old links.
    from fastapi.responses import RedirectResponse

    @app.get("/dashboard")
    @app.get("/dashboard/")
    async def _legacy_dashboard():
        return RedirectResponse("/app")

    app_dir = Path(__file__).parent / "static_app"
    if app_dir.exists():
        app.mount("/app", StaticFiles(directory=str(app_dir), html=True), name="app")

    @app.get("/")
    async def root() -> dict:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()

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
    adversary as adversary_routes, allocation, assistant, auth, backtest, broker, commodities, decision_feed, entities, fees, gfinance, google_auth, health, intake, jobs, lag, learning, market, push, rules, screener, strategy,
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
                    # plans.strategy / strategy_sleeve_pct are DROPPED here, not
                    # added. They were the single-sleeve model and `plan_sleeves`
                    # replaced them; the last thing they said in production was
                    # 23 points out of date. Dropped in the same list that used
                    # to create them, because this list -- not alembic -- is what
                    # actually runs against production. IF EXISTS so a database
                    # that never had them, or a redeploy, is a no-op.
                    "ALTER TABLE plans DROP COLUMN IF EXISTS strategy",
                    "ALTER TABLE plans DROP COLUMN IF EXISTS strategy_sleeve_pct",
                    # create_all builds missing TABLES but never adds a column
                    # to one that already exists, so a model that gains a field
                    # leaves production reading a schema it does not have.
                    # strategy_backtests gained these in migration 0011 and the
                    # deploy went out before the migration ran: every SELECT
                    # raised UndefinedColumnError, which took out /strategies,
                    # /strategies/backtests and the backtest refresh. Running
                    # alembic by hand is the documented answer and it failed
                    # twice -- once against the local sqlite URL in .env, once
                    # on DNS, because Railway's DATABASE_URL names a private
                    # host that does not resolve outside their network.
                    "ALTER TABLE strategy_backtests ADD COLUMN IF NOT EXISTS "
                    "last_error VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE strategy_backtests ADD COLUMN IF NOT EXISTS "
                    "last_error_at TIMESTAMPTZ",
                    # AND IT HAPPENED AGAIN, to the very next column this table
                    # gained. Migration 0015 added benchmark_ticker on
                    # 2026-08-15 and was never mirrored here, so production has
                    # been raising
                    #
                    #   UndefinedColumnError: column
                    #   strategy_backtests.benchmark_ticker does not exist
                    #
                    # on every read of this table since. `get_many` catches it
                    # and returns {} by design -- a strategy LIST must not die
                    # because a side table is behind -- and that is exactly why
                    # nobody noticed: the app degraded silently to "no strategy
                    # has ever been measured", and EVERY measured figure in it
                    # has been missing for ten days. /strategies carried the
                    # reason in `backtest_store_error` the whole time and no
                    # screen rendered it.
                    #
                    # The note above already says running alembic by hand is the
                    # documented answer and that it failed twice. This list IS
                    # the mechanism that keeps production's schema current, so a
                    # migration that adds a column and does not appear here has
                    # not really shipped. Guarded now by
                    # tests/test_startup_ddl_covers_migrations.py.
                    "ALTER TABLE strategy_backtests ADD COLUMN IF NOT EXISTS "
                    "benchmark_ticker VARCHAR(16) DEFAULT ''",
                    # P4: strategy_signal rules pin the strategy they follow.
                    "ALTER TABLE trading_rules ADD COLUMN IF NOT EXISTS "
                    "strategy_id VARCHAR(40)",
                    # PR 3: the trade ledger becomes writable. `transactions`
                    # already existed (unwritten), so create_all will not touch
                    # it -- these columns have to be added by hand, and
                    # position_id must stop being NOT NULL/CASCADE or a full
                    # exit deletes the record of its own sale.
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "subject VARCHAR(255)",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "ticker VARCHAR(24)",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "market VARCHAR(16)",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "value_ils NUMERIC(20, 6) DEFAULT 0",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "tax_ils NUMERIC(20, 6) DEFAULT 0",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "source VARCHAR(40)",
                    "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS "
                    "note VARCHAR(255)",
                    "ALTER TABLE transactions ALTER COLUMN position_id "
                    "DROP NOT NULL",
                    # And the CASCADE itself. Dropping NOT NULL is not enough:
                    # the constraint still says "delete the row when its position
                    # goes", which erases the record of the very sale that
                    # removed it. The name is generated, so it has to be looked
                    # up rather than guessed.
                    "DO $$ DECLARE c text; BEGIN "
                    "SELECT conname INTO c FROM pg_constraint "
                    "WHERE conrelid = 'transactions'::regclass AND contype = 'f' "
                    "AND conname <> 'fk_transactions_position' LIMIT 1; "
                    "IF c IS NOT NULL THEN "
                    "EXECUTE format('ALTER TABLE transactions DROP CONSTRAINT %I', c); "
                    "END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
                    "WHERE conname = 'fk_transactions_position') THEN "
                    "ALTER TABLE transactions ADD CONSTRAINT fk_transactions_position "
                    "FOREIGN KEY (position_id) REFERENCES positions(id) "
                    "ON DELETE SET NULL; END IF; END $$;",
                    "CREATE INDEX IF NOT EXISTS ix_transactions_subject "
                    "ON transactions (subject)",
                    "CREATE INDEX IF NOT EXISTS ix_transactions_ticker "
                    "ON transactions (ticker)",
                    # C1 (plan_sleeves) needs no entry here. create_all above
                    # DOES build a missing table; what it never does is add a
                    # column to a table that already exists, which is the whole
                    # reason this list exists. A redundant CREATE TABLE would
                    # imply a gap that is not there.
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



class _NoCacheShell(StaticFiles):
    """Serve the PWA shell and service worker with revalidation forced.

    Default StaticFiles sends no Cache-Control, so browsers heuristically cache
    index.html and sw.js. Combined with the service worker caching the shell,
    deploys repeatedly failed to reach installed clients: the app kept running
    old JavaScript while reporting a successful deployment. Static hashed assets
    (icons, manifest) keep normal caching.
    """

    async def get_response(self, path: str, scope):  # type: ignore[override]
        response = await super().get_response(path, scope)
        if path.endswith((".html", "sw.js")) or path in ("", "."):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


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
    app.include_router(rules.router)

    # Legacy /dashboard consolidated into /app (Phase G): keep a redirect for old links.
    from fastapi.responses import RedirectResponse

    @app.get("/dashboard")
    @app.get("/dashboard/")
    async def _legacy_dashboard():
        return RedirectResponse("/app")

    app_dir = Path(__file__).parent / "static_app"
    if app_dir.exists():
        app.mount("/app", _NoCacheShell(directory=str(app_dir), html=True), name="app")

    @app.get("/")
    async def root() -> dict:
        return {"service": settings.app_name, "version": settings.app_version, "docs": "/docs"}

    return app


app = create_app()

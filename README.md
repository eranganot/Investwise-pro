# InvestWise Pro — Multi-Agent Wealth Operating System (v22.1)

A cloud-native FastAPI backend + React dashboard for net-wealth optimization.
**This is the Phase 0 skeleton:** the full Position Lifecycle state machine, all
11 database tables, typed engine stubs, and a dashboard shell. Engine logic is
added phase by phase (see `EXECUTION_PLAN.md`).

> **Note:** This software produces recommendations from the rules and parameters
> *you* configure. It is not financial advice. Tax rates/thresholds are
> config-driven (`.env`) and should be confirmed with your accountant.

## Architecture

```
app/
  core/        config (env-driven) + async SQLAlchemy engine
  models/      11 SQLAlchemy tables (Section 4)
  schemas/     Pydantic state machine (5 stages) + output contract (Section 7)
  engines/     Tax · Lag · Risk · Decision · WHS · Simulation + orchestrator
  api/routes/  health + demo decision feed
  main.py      FastAPI app factory
frontend/      React + Vite dashboard shell
tests/         state machine tests
```

The lifecycle (Section 1) is type-enforced — each stage embeds the previous
stage as a typed field, so **no stage can be skipped**:

```
Detected → Vetted → Optimized → Ranked → Displayed
              └─ veto_flag → Vetoed (terminal, never shown)
```

## Quick start — option A: Docker (recommended, cloud-native)

```bash
# from investwise-pro/
docker compose up --build
# API:   http://localhost:8000   (docs at /docs)
# DB:    Postgres 16 on :5432
```

## Quick start — option B: local Python

```bash
# 1. install dependencies
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt                 # for tests

# 2. point DATABASE_URL at a running Postgres (or use docker compose up db)
cp .env.example .env

# 3. run the dev server
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (proxies /api and /health to :8000)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

## Database migrations (production)

Dev auto-creates tables (`AUTO_CREATE_TABLES=true`). For production, use Alembic:

```bash
alembic revision --autogenerate -m "init"
alembic upgrade head
```

## Deploy to Railway

1. Push this repo to GitHub.
2. Railway → New Project → Deploy from GitHub repo (uses `railway.json` + `Dockerfile`).
3. Add the **Postgres** plugin — Railway injects `DATABASE_URL` (auto-normalized to asyncpg).
4. Set `FRONTEND_ORIGIN` to your deployed frontend URL.

## Endpoints (Phase 0)

| Method | Path                          | Purpose                              |
|--------|-------------------------------|--------------------------------------|
| GET    | `/`                           | service info                         |
| GET    | `/health`                     | health check                         |
| GET    | `/health/ready`               | readiness probe (DB check)           |
| GET    | `/api/v1/market/quote`        | Market quote (resilient provider)    |
| GET    | `/api/v1/fx/rate`             | FX rate                              |
| GET    | `/api/v1/research/events`     | Research Agent evidence (read-only)  |
| GET    | `/api/v1/providers/health`    | Provider circuit-breaker status      |
| GET    | `/api/v1/decision-feed/demo`  | runs the lifecycle on sample signals |
| GET    | `/api/v1/risk/preview`        | Monte Carlo risk + veto decision     |
| POST   | `/api/v1/lag/scan`            | Lag divergence scan (depth-ranked)   |
| GET    | `/api/v1/whs`                 | Wealth Health Score (weighted)       |
| GET    | `/api/v1/simulation`          | Forward projection (CPI/FX/horizon)  |
| POST   | `/api/v1/safety/check`        | Safety Layer (concentration/liquidity) |
| POST   | `/api/v1/actions`             | Record accepted/ignored (learning)   |
| GET    | `/api/v1/learning/profile`    | Personalization profile              |
| POST   | `/api/v1/intake/portfolio`    | Ingest positions (JSON)              |
| POST   | `/api/v1/intake/portfolio/csv`| Ingest positions (CSV upload)        |
| GET    | `/api/v1/portfolio`           | List persisted positions             |
| GET    | `/api/v1/entities`            | List entities (Personal/Spouse/Corp) |
| GET    | `/api/v1/auth/status`         | Whether API-key auth is enabled      |
| POST   | `/api/v1/allocation/analyze`  | SAA/TAA drift + cost-adjusted rebalance |
| GET    | `/api/v1/health-check`        | Portfolio Health Check (≤5 opps)       |
| GET    | `/api/v1/decision-feed/weekly`| Weekly feed (cap 10, categorized)    |
| GET    | `/api/v1/tax/review`          | Tax Optimization Review (₪ savings)    |
| GET    | `/api/v1/risk/alerts`         | Risk Alert Center (5 vectors)        |
| POST   | `/api/v1/scenario`            | Scenario Planning (macro stress)     |

Every Decision Feed item also carries an XAI **explanation** (why_now, supporting/contradicting factors, assumptions, confidence breakdown, expected outcomes, failure conditions).
| POST   | `/api/v1/decision-feed/generate` | Run pipeline + persist to Postgres |
| GET    | `/api/v1/decision-feed/latest`   | Read back the latest persisted feed |
| GET    | `/dashboard/`                 | Self-contained web dashboard         |
| GET    | `/docs`                       | OpenAPI / Swagger UI                 |

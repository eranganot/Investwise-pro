# InvestWise Pro

A personal wealth cockpit: a FastAPI backend and a single-file installable PWA. Each day it turns your holdings, your
plan and live market data into **a short list of sized, funded, explained cards**, each executable with one tap on
the book it tracks. Everything is valued in **ILS**. Behind the cards sits one goal model that answers "what is my chance of reaching
my goal, and what would change it?"

> **Not financial advice.** Every figure traces to your data, a market price, or a labelled assumption; the app
> never invents numbers. Accepting a card updates the book InvestWise tracks. **It never places a brokerage order.**
> Tax rates are config-driven; confirm them with your accountant.

## Project status (2026-10-08)

| | |
|---|---|
| **Production** | Live on Railway, single user, live Yahoo + Frankfurter data. Last product PR: **PR 104d.1** (every card threshold is a setting) |
| **Active plan** | [Execution plan v3: hardening](docs/plans/EXECUTION_PLAN_2026-10-08_hardening.md) (PR 105–119). It starts with two latent currency-sizing bugs, then truthful scheduled jobs, auth hardening, and legacy removal. Evidence and reproducing probes: [`docs/plans/evidence-2026-10-08/`](docs/plans/evidence-2026-10-08/) |
| **Open work** | [docs/BACKLOG.md](docs/BACKLOG.md) |
| **Changelog** | [STATUS.md](STATUS.md), one entry per shipped PR |

## Documentation

**→ [docs/README.md](docs/README.md)** is the entry point. In short:

| | |
|---|---|
| [Product](docs/PRODUCT.md) | What it is, who it's for, the principles |
| [Architecture](docs/ARCHITECTURE.md) | System, layers, request flow, jobs |
| [Domain logic](docs/DOMAIN.md) | How cards are made, funding, the goal, the invariants |
| [Development](docs/DEVELOPMENT.md) | Run, test, migrate, ship (incl. per-PR smoke) |
| [Operations](docs/OPERATIONS.md) | Production, deploy, secrets, runbook |
| [Decisions](docs/DECISIONS.md) · [History](docs/HISTORY.md) · [Backlog](docs/BACKLOG.md) | Why, how we got here, what's next |
| [STATUS.md](STATUS.md) | Changelog: one entry per shipped PR |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
docker compose up -d db                                   # Postgres 16 on :5432
alembic upgrade head
uvicorn app.main:app --reload --port 8000                 # UI: http://localhost:8000/app/   API docs: /docs
```

Or run everything in Docker with `docker compose up --build`. Data defaults to deterministic **illustrative** prices; set
`MARKET_DATA_PROVIDER=yahoo` and `FX_PROVIDER=frankfurter` for live data. See [Development §2–3](docs/DEVELOPMENT.md).

## Tests

```bash
pytest -q                     # ~2,290 tests, SQLite (CI job "test")
ruff check app tests          # CI job "lint"
DATABASE_URL=postgresql+asyncpg://investwise:investwise@127.0.0.1:5432/investwise pytest -q   # CI job "test-postgres"; run it before pushing
```

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Postgres 16 / asyncpg · Alembic · APScheduler · NumPy · vanilla-JS PWA
with Chart.js · Web Push (VAPID) · Google OAuth · Gemini (optional) · Yahoo / Frankfurter / FMP market data · Docker on Railway.

## Repository layout

```
app/
  main.py          app factory, lifespan, middleware, /app static mount
  core/            settings, DB engine, JWT/RBAC, audit, metrics
  models/          29 SQLAlchemy tables
  api/routes/      32 routers (~143 endpoints)
  services/        business logic: Today cards, funding, plan/goal/frontier, rules, ledger, pricing, push
  engines/         pure computation: frontier, backtest, Monte Carlo, tax, risk, blend …
  agents/          Research, Opportunity, Fee, Allocation, Adversary
  providers/       market data + FX behind a resilience tier
  worker/          APScheduler jobs (+ vestigial Celery)
  static_app/      the whole UI: index.html, sw.js, manifest, icons
alembic/           migrations 0001–0028 (run as Railway preDeployCommand)
tests/             ~2,290 tests, named as claims
scripts/           PowerShell smokes, ops checks, operator CLIs (see scripts/README.md)
qa/                Pixel 9 manual QA lists
docs/              documentation (+ archive/ of superseded plans and status)
```

## Deploy

Push to `main` and Railway builds the `Dockerfile`, runs `alembic upgrade head` as the pre-deploy step, and swaps traffic once
`/health/ready` passes. **CI does not gate the deploy.** After every push, run that PR's smoke:
`.\scripts\smoke\smoke-prNN-<slug>.ps1 -Sha <short-sha>`. See [Operations](docs/OPERATIONS.md).

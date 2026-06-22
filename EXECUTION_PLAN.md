# InvestWise Pro — Execution Plan (for confirmation)

**Project:** InvestWise Pro — Multi-Agent Wealth Operating System (v22.1)
**SuperAdmin:** Eran Ganot
**Location:** `Investments/investwise-pro/`
**Status:** ✅ Built & live. (Superseded: the React/Vite scaffold was dropped in favor of two self-contained single-file UIs — /app and /dashboard — served directly by FastAPI.)

> This document is the plan only. Once you confirm (or adjust) it, I start with Phase 0.

---

## 1. Decisions locked in

| Question | Your answer |
|---|---|
| Build depth | Plan covers **all 6 engines**; MVP ships as a **runnable skeleton**, engines added phase by phase. |
| Database | **PostgreSQL + async SQLAlchemy** from day one. |
| Location | New folder: `Investments/investwise-pro/`. |
| Frontend | **Backend + frontend scaffold** (React/Vite), wired to the Figma `/dashboard` concept. |

---

## 2. Tech stack

**Backend**
- Python 3.12, FastAPI (fully async)
- SQLAlchemy 2.0 (async ORM) + `asyncpg` driver → PostgreSQL
- Alembic (schema migrations)
- Pydantic v2 (DTOs + the strictly-typed state machine) and `pydantic-settings` (config from `.env`)
- NumPy (Monte Carlo for the Risk engine)
- pytest + pytest-asyncio + httpx (tests)

**Frontend**
- React + Vite + TypeScript + Tailwind CSS (matches the style Figma Make emits)
- TanStack Query (API data fetching), Recharts (WHS / simulation charts)
- One `/dashboard` route to start, mirroring your Figma concept

**Ops (cloud-native)**
- Multi-stage `Dockerfile`, `docker-compose.yml` (api + postgres) for one-command local dev
- Health/readiness endpoints, Alembic migrations on startup, 12-factor `.env` config

---

## 3. Repository structure

```
investwise-pro/
├── docker-compose.yml          # api + postgres
├── Dockerfile
├── .env.example
├── pyproject.toml
├── alembic.ini
├── README.md
├── EXECUTION_PLAN.md           # this file
├── alembic/versions/
├── app/
│   ├── main.py                 # FastAPI app factory, routers, CORS
│   ├── core/
│   │   ├── config.py           # Pydantic settings
│   │   ├── database.py         # async engine + session
│   │   └── logging.py
│   ├── models/                 # 11 SQLAlchemy tables (Section 4)
│   │   ├── base.py  user.py  entity.py  account.py  bucket.py
│   │   ├── position.py  transaction.py  tax_profile.py
│   │   ├── decision_feed.py  decision_item.py
│   │   ├── whs_snapshot.py  user_action.py
│   ├── schemas/
│   │   ├── state_machine.py    # the 5 typed lifecycle stages
│   │   └── output_contract.py  # Section 7 recommendation JSON
│   ├── engines/
│   │   ├── state_machine.py    # orchestrator
│   │   ├── tax_engine.py  lag_engine.py  risk_engine.py
│   │   ├── decision_engine.py  whs_engine.py  simulation_engine.py
│   ├── agents/                 # Alpha / Systems / Adversary / UX (later phase)
│   └── api/routes/             # health, intake, positions, decision_feed, simulation
├── frontend/                   # Vite + React dashboard
└── tests/                      # one test file per engine + state machine
```

---

## 4. The State Machine (Section 1) — how type-safety is enforced

Each lifecycle stage is its own Pydantic model, and each transition is a function that **only accepts the previous stage's type and returns the next**. Because the function signatures require the predecessor object, a stage physically cannot be skipped — it won't type-check or run.

```
DetectedSignal ─▶ VettedSignal ─▶ OptimizedSignal ─▶ RankedSignal ─▶ DisplayedItem
   (Lag)            (Risk)           (Tax)             (Decision)       (Feed)
                       │
                       └─ veto_flag=True ─▶ VetoedSignal (terminal, never displayed)
```

- **DETECTED** — Lag engine emits a signal (Depth 1/3 divergence or Commodity Spot delta).
- **VETTED** — Risk engine attaches Monte Carlo results, Probability of Ruin, volatility caps, and may set `veto_flag`.
- **OPTIMIZED** — Tax engine attaches net-after-tax numbers and surtax offset.
- **RANKED** — Decision engine computes Impact Score and confidence.
- **DISPLAYED** — UX-formatted item ("Growth" vs "Bulletproof") if it clears Impact ≥ 20 and Confidence ≥ 60.

This is the heart of Phase 0 and the first thing tested.

---

## 5. Database schema (Section 4) — 11 tables

`users · entities · accounts · buckets · positions · transactions · tax_profiles · decision_feeds · decision_items · whs_snapshots · user_actions`

All created in Phase 0 as async SQLAlchemy models with an initial Alembic migration. Every engine output maps to a storable, JSON-compatible row, per your rule.

---

## 6. Phased roadmap

Each phase is independently runnable and ends with a **verification gate** (tests green + a manual check) before the next begins.

### Phase 0 — Foundation & runnable skeleton (the MVP)
- Full project structure, async DB connection, `docker-compose up` brings up API + Postgres.
- All 11 SQLAlchemy models + initial Alembic migration.
- Pydantic state machine with all 5 typed stages and the veto path. Engines exist as **typed stubs returning `"Awaiting Data"`** (honoring the SYSTEMS agent rule — no hallucinated numbers).
- Output-contract model (Section 7).
- Frontend Vite/React scaffold with a `/dashboard` shell that calls the health endpoint.
- **Tests:** state machine completes a full linear pass; attempting to skip a stage fails.
- **Gate:** `docker compose up` works, `pytest` green, dashboard loads.

### Phase 1 — Tax Engine (Section 4.1)
- 25% CGT, 5% surtax above the high-income threshold, net-after-tax, ₪ saved / ₪ deferred, surtax offset, loss carry-forward, trapped-profit flags.
- Threshold and rates are **config-driven, not hardcoded** (see open questions).
- **Gate:** worked numeric examples verified in tests.

### Phase 2 — Risk Engine (Section 4.4)
- Monte Carlo (10k runs, NumPy), Probability of Ruin, Max Drawdown 20% cap, Volatility 15% cap.
- Produces `veto_flag` → drives the state machine's veto path. **Risk overrides return.**
- **Gate:** seeded deterministic test; veto triggers correctly.

### Phase 3 — Lag Engine (Section 4.2)
- Depth 1–3 mapping, Spot vs TASE/NYSE divergence, "Backbone vs Hype" ratio prioritizing Depth 3.
- Data intake adapters (CSV first per Section 5).

### Phase 4 — Decision Engine + WHS (Sections 4.3, 4.5)
- Impact Score = (0.4·R + 0.3·T + 0.3·Risk) / Complexity; display gate Impact ≥ 20, Confidence ≥ 60.
- WHS weighting 0.25 Risk / 0.25 Tax / 0.20 Alloc / 0.15 Liq / 0.15 Thematic.
- Persists `decision_feeds` / `decision_items`; formats "Growth" vs "Bulletproof" paths.

### Phase 5 — Simulation Engine (Section 4.6)
- Horizon month / quarter / year; inputs CPI, FX, Spot Volatility; scenario projection endpoint.

### Phase 6 — Agent War Room, Safety Layer, Learning Loop (Sections 6, 8, 9)
- Adversary `risk_critique` on every feed item + veto enforcement.
- Safety: concentration, liquidity-failure, irrational-decision flags, "No Action Recommended".
- Learning loop: `user_actions` tracking → personalizes complexity/style of future recs.

### Phase 7 — Full Figma Decision Feed (frontend)
- Pull the Figma design context, build the real dashboard: decision-feed cards, Growth/Bulletproof toggle, WHS snapshot, simulation controls — wired to all endpoints.

---

## 7. Testing & verification strategy

- Unit tests per engine with worked numeric examples.
- State machine: linear-pass test + skip-prevention test + veto-path test.
- API integration tests via httpx.
- A verification gate closes every phase; I won't move on with a red suite.

---

## 8. Assumptions & open questions (please confirm or correct)

1. **Tax parameters.** The spec lists 25% CGT, 5% surtax above ~₪721k. Israeli tax rules change, so I'll make all rates/thresholds **configurable** rather than hardcoded, and flag that the live numbers should be confirmed with your accountant. → *OK to proceed config-driven?*
2. **"Probability of Ruin" definition.** I'll implement it as the share of Monte Carlo paths breaching the 20% max-drawdown floor unless you define it differently. → *Acceptable default?*
3. **Market data for the Lag engine.** Phase 3 starts with **CSV intake** (TASE/NYSE/Spot). A live feed can come later — the Coupler.io connector you have available is one option. → *Start with CSV?*
4. **Scope of users.** Single SuperAdmin (you) for now, multi-entity (Personal/Spouse/Corp) modeled in data but no multi-user auth yet. → *Correct?*

None of these block Phase 0 — they matter from Phase 1 onward.

---

## 9. A note on scope of my role

I'm building the software and the calculation logic exactly as you've specified it. The recommendations the system produces are the output of *your* rules and parameters — not financial advice from me. Tax thresholds and rates in particular should be validated against current law / your accountant before you act on any output.

---

## 10. What I need from you to start

Reply with **"go"** to begin Phase 0, or tell me what to change. When you approve, my first deliverable will be the full runnable skeleton: `docker compose up` for API + Postgres, all 11 models migrated, the typed state machine with passing tests, and a dashboard shell — plus the exact `pip install` / run commands as requested in your execution command.
```

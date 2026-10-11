# API reference

> ~143 endpoints across 32 routers in `app/api/routes/`, plus 4 in `app/main.py`. The live OpenAPI is at
> **`/docs`** on a development instance (off in production since PR 110). Since PR 110 every write route needs
> ANALYST+ (SUPERADMIN can do everything), so read the **U** marks on writes below as ANALYST. This page groups the endpoints by purpose and marks which ones the PWA
> uses. Written 2026-10-07 at PR 104d.1.

**Auth column:**
- **U**: `acting_user` (READ_ONLY+ when `REQUIRE_AUTH`; resolves the user and activates their card thresholds)
- **A**: also `require_role(ANALYST)`
- **S**: SUPERADMIN
- **—**: no auth dependency (public)

Scripts authenticate with `X-Agent-Key` (SUPERADMIN).

**UI:** ✅ = called by `index.html`.

## Health and ops

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET | `/health` | — | | `status`, `version`, **`commit`** (deployed SHA), `environment`, **`schema_revision`**, `builds_own_schema`. Always 200 |
| GET | `/health/ready` | — | | `SELECT 1`; 503 when the DB is down. **Railway healthcheck** |
| GET | `/metrics` | — | | Prometheus |
| GET | `/api/v1/audit` | S | | Recent `audit_log` rows |
| GET | `/api/v1/data-status` | — | ✅ | Live vs illustrative data, effective price source |
| GET | `/api/v1/providers/health` | — | | Circuit-breaker state per provider tier |
| GET | `/api/v1/push/status` | U | | Push diagnostics + scheduler `JOB_STATE` + VAPID pin state |

## Auth

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET | `/login` | — | ✅ | Login page |
| GET | `/auth/google/login`, `/auth/google/callback` | — | ✅ | Google OAuth; allowlist; sets the `iw_session` cookie |
| GET | `/auth/logout` | — | ✅ | Deletes the cookie (does not revoke the JWT) |
| GET | `/api/v1/auth/me` | U | ✅ | Current principal |
| GET | `/api/v1/auth/status` | — | | `{auth_enabled: REQUIRE_AUTH}` |
| POST | `/api/v1/auth/token`, `/auth/refresh` | — | | Password login (off unless `AUTH_PASSWORD`) / refresh rotation |
| POST | `/api/v1/auth/m2m` | S | | 1-year machine token |

## Today (recommendations)

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET | `/api/v1/recommendations` | U | ✅ | **Builds Today.** `?simulate_cash=` previews the cash card only. *Has write side effects* ([DOMAIN.md §4](DOMAIN.md#4-the-today-pipeline)) |
| POST | `/api/v1/recommendations/{id}/accept` | U | ✅ | Execute the card on the tracked book. `?choice=`, `level=`, `alloc=`; body = rendered card state |
| POST | `/api/v1/recommendations/{id}/dismiss` | U | ✅ | Ignore (7-day mute) |
| POST | `/api/v1/recommendations/{id}/restore` | U | ✅ | Bring one card back |
| POST | `/api/v1/recommendations/restore`, `/restore-completed` | U | | Bring back all ignored / done cards |
| POST | `/api/v1/recommendations/headsups/{id}/dismiss` | U | ✅ | Dismiss a heads-up (permanent) |
| POST | `/api/v1/holding-review/refresh` | A | | Re-measure the holding review now |
| GET | `/api/v1/health-check` | U | ✅ | Health hero (On plan + On track) |
| GET | `/api/v1/risk/alerts` | U | ✅ | "Your biggest risk" |
| GET | `/api/v1/war-room` | U | ✅ | Agents tab transcript |

## Plan, goal, frontier

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET / PUT | `/api/v1/plan` | U / A | ✅ | The plan + stats + goal; PUT edits settings including `{thresholds:{k: v\|null}}` (422 if out of bounds) |
| GET | `/api/v1/plan/projection` | U | ✅ | Goal projection (with/without deposits) |
| GET | `/api/v1/mix` | U | ✅ | Asset-class mix vs target |
| GET | `/api/v1/plan/sleeves` | U | ✅ | Sleeves + core breakdown |
| POST | `/api/v1/plan/sleeves/fund` | A | ✅ | Fund under-funded sleeves (`dry_run` defaults to true) |
| DELETE | `/api/v1/plan/sleeves/{id}` | A | ✅ | Remove a sleeve (sells nothing) |
| POST | `/api/v1/plan/sleeves/{id}/swap` | A | | Swap a sleeve's strategy at the same size |
| GET | `/api/v1/plan/core/options` | U | ✅ | Core candidates + consequences |
| POST / DELETE | `/api/v1/plan/core` | A | ✅ | Set / clear the core |
| GET | `/api/v1/plan/frontier` | U | ✅ | Latest stored frontier/goal verdict |
| POST | `/api/v1/plan/frontier/refresh` | A | ✅ | Re-solve now (~10–20 s) |
| POST | `/api/v1/plan/frontier/preview` | U | ✅ | Odds as they would be with the posted settings (sheets) |
| GET | `/api/v1/plan/frontier/inputs` | U | | Inputs with provenance |
| GET | `/api/v1/plan/target` | U | ✅ | Target solver ("What would it take?") |
| GET | `/api/v1/plan/target/split`, `/cores`, `/sleeve-count`, `/accepted`, `/applications` | U | partly | Solver variants and history |
| POST | `/api/v1/plan/target/apply`, `/undo` | A | ✅ | Write / undo sleeve sizes (`confirm=true`) |
| GET | `/api/v1/plan/walk-forward` | U | | Walk-forward sweep |

## Portfolio, cash, ledger

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET | `/api/v1/portfolio` | U | ✅ | Holdings with FX-normalised values |
| POST | `/api/v1/intake/portfolio`, `/intake/portfolio/csv` | A | ✅ | Ingest positions; `GET /intake/template.csv` |
| POST | `/api/v1/portfolio/add` | A | ✅ | Add or top up a holding by ₪ amount |
| PUT | `/api/v1/portfolio/position/{id}` | U | ✅ | Edit a holding |
| DELETE | `/api/v1/portfolio/position?ticker=&market=` | U | ✅ | Remove a holding |
| POST | `/api/v1/portfolio/refresh-prices` | A | ✅ | Reprice now |
| GET / POST | `/api/v1/portfolio/cash` | U / A | ✅ | Read / set liquid cash |
| GET / POST | `/api/v1/portfolio/contributions` | U / A | | Deposits/withdrawals ledger |
| GET / POST / PUT / DELETE | `/api/v1/portfolio/standing-orders[/{id}]`, `POST …/run` | U / A | ✅ | Monthly deposits |
| GET | `/api/v1/portfolio/ledger` | U | | Transactions, newest first |
| GET / POST | `/api/v1/portfolio/ledger/repair`, `/ledger/repair-held-cash` | U / A | | Preview / apply history repairs (`confirm`) |
| GET / POST | `/api/v1/portfolio/reconcile/preview`, `/apply`, `/baseline` | U / A | | Share-count reconciliation |
| GET | `/api/v1/portfolio/nav-history?range=` | U | ✅ | Real recorded NAV |
| POST | `/api/v1/portfolio/nav-history/snapshot` | A | ✅ | "Record today now" |
| GET | `/api/v1/portfolio/position-history` | U | ✅ | Per-holding chart |
| POST | `/api/v1/portfolio/performance`, `/portfolio/risk` | A | ✅ | Backfilled performance vs benchmark / VaR, CVaR, beta |

## Rules

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET / POST | `/api/v1/rules` | U | ✅ | List / create |
| PATCH / DELETE | `/api/v1/rules/{id}` | U | ✅ | Edit (may return `needs_confirm` + `suggested_level`) / delete |
| POST | `/api/v1/rules/{id}/toggle` | U | ✅ | Pause / arm |
| GET | `/api/v1/rules/suggestions`, `/rules/events` | U | ✅ | Suggested rules / history |
| POST | `/api/v1/rules/evaluate` | U | | Evaluate now |

## Strategies, markets, AI, push

| M | Path | Auth | UI | Purpose |
|---|---|---|---|---|
| GET | `/api/v1/strategies`, `/strategies/backtests` | — | ✅ | Catalog + stored backtests |
| GET / POST | `/api/v1/strategies/{id}/preview`, `/apply`, `/load-basket` | U / A | ✅ | What changes / add sleeve / fund or replace book |
| POST | `/api/v1/strategies/backtests/refresh` | A | | Recompute backtests |
| GET / POST | `/api/v1/strategies/signal`, `/signal/ack` | U / A | | Sleeve signal state |
| GET | `/api/v1/commodities` | — | ✅ | Commodity catalog |
| GET | `/api/v1/markets/futures`, `/markets/summary` | — | ✅ | Futures regime / AI macro summary |
| GET / POST | `/api/v1/ai/portfolio-summary`, `/ai/holding/{t}/summary`, `/ai/holding/{t}/research` | U | ✅ | Gemini summaries |
| POST | `/api/v1/ask` | U | ✅ | Ask InvestWise (grounded) |
| GET | `/api/v1/digest` | U | ✅ | Digest |
| GET | `/api/v1/research/events` | U | ✅ | "What's moving" (empty: no economic source) |
| POST | `/api/v1/whatif`, `/scenario` | U | ✅ | What-if sliders / macro stress |
| GET | `/api/v1/push/public-key` | — | ✅ | VAPID public key |
| POST | `/api/v1/push/subscribe`, `/unsubscribe`, `/test`, `/check` | U | ✅ | Device subscription and checks |
| POST | `/api/v1/push/run-all` | S | | Fan out evaluate/digest to all users |
| GET | `/api/v1/push/digest/preview` | U | | Digest preview |

## Legacy / not used by the UI

Mounted, partly tested, **unused by the PWA**. Candidates for deletion ([BACKLOG.md](BACKLOG.md)):

`/api/v1/decision-feed/demo|generate|latest|weekly` (the weekly one silently falls back to demo data), `/actions`,
`/learning/profile`, `/tax/review` (deleted in PR 113), and `/adversary/diagnostics` (kept, SUPERADMIN since PR 110).
**Deleted in PR 112:** `/lag/scan`, `/whs`, `/simulation`, `/safety/check`, `/tax/preview`, `/risk/preview`,
`/allocation/analyze`, `/backtest`, `/fees`, `/screener`, `/entities` (`/auth/status` moved to `auth.py`, same path),
`/market/quote`, `/fx/rate`, `/jobs/*`, `/broker/connect|sync`.

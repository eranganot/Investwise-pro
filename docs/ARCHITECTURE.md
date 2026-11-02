# Architecture

> How InvestWise Pro is built and how a request moves through it. Written 2026-10-07 at
> PR 104d.1 (`96427f5`); `file:line` references are to that commit. When code and this page
> disagree, the code wins. Fix the page in the same PR.

Related: [DATA_MODEL.md](DATA_MODEL.md) · [API.md](API.md) · [DOMAIN.md](DOMAIN.md) ·
[AGENTS.md](AGENTS.md) · [FRONTEND.md](FRONTEND.md) · [OPERATIONS.md](OPERATIONS.md)

---

## 1. The picture

```
 Pixel 9 / browser (installed PWA)
   │  same-origin fetch, httpOnly session cookie (JWT)
   ▼
 ┌──────────────────────── one container on Railway ─────────────────────────┐
 │ uvicorn app.main:app (single process, single worker)                      │
 │                                                                           │
 │  /app  ── static PWA shell (app/static_app/index.html, sw.js, manifest)   │
 │  /api/v1/* ── 32 routers ──► services/ (business logic, ~70 modules)      │
 │                               │    ├─► engines/  (pure computation)       │
 │                               │    ├─► agents/   (judgement + veto)       │
 │                               │    └─► providers/ (market data, FX)       │
 │                               ▼              │ resilience tier            │
 │                      SQLAlchemy async        ▼                            │
 │  APScheduler (11 jobs, threads)     Yahoo · FMP · Frankfurter · Gemini    │
 │  warm_loop (asyncio task)                                                 │
 └───────────────┬───────────────────────────────────────────────────────────┘
                 ▼
          Railway Postgres 16  (schema owned by Alembic, run as preDeployCommand)
```

- **One process does everything:** the API, the static UI, the scheduler and the cache
  warm-up. Nothing else runs: no Redis, no worker, no separate frontend build. This
  assumes a **single replica**. Two replicas would double-run every scheduled job.
- **Postgres in production, SQLite in the default test run.** CI runs the suite on both.
  The difference between them is a recurring source of bugs ([DECISIONS.md](DECISIONS.md) D27).
- **No real brokerage.** "Accept" on a card rewrites the *tracked book* in Postgres and writes
  ledger rows. No order is ever placed. (The broker scaffold, `app/brokers/`, was deleted in PR 112.)

## 2. Layers and directories

| Path | Role | Rule of thumb |
|---|---|---|
| `app/main.py` | App factory, lifespan, middleware, router mounts, `/app` static mount, `/dashboard` → `/app` redirect | Thin. |
| `app/core/` | Settings (`config.py`), DB engine/session (`database.py`), JWT + RBAC (`auth.py`), audit, metrics, offload, Decimal helpers | No business logic. |
| `app/models/tables.py` | All 29 SQLAlchemy tables | Schema changes need an Alembic migration ([DEVELOPMENT.md §6](DEVELOPMENT.md#6-database-migrations)). |
| `alembic/versions/` | 28 linear migrations `0001`→`0028` | `0001` runs `create_all` from current models; later ones are guarded add-only. |
| `app/api/routes/` | 32 routers, ~143 endpoints | Delegate to services; services never import routes (`test_services_never_import_routes`). PR 118a moved `plan.py`'s logic to `plan_view`, `projection_service`, `book_target.mix_report` (549 lines left) and the war-room payload to `services/war_room`; PR 118b moved `intake.py`'s (953 → 635 lines) to `portfolio_view.holdings`, `intake_service.add_by_amount`/`row_to_position`/`persist`, `ledger_service.last_buy`, `standing_order_service.orders_payload`. |
| `app/api/deps.py` | `acting_user`: resolves the user **and** activates the plan's card thresholds for the request | Every user-scoped route depends on it. |
| `app/services/` | Business logic: recommendations (Today), funding, plan/goal/frontier, rules, ledger, pricing, push, war room… | The app lives here. See [DOMAIN.md](DOMAIN.md). |
| `app/engines/` | Deterministic calculators: tax, risk (Monte Carlo), frontier, backtest, simulation, scoring… | Same inputs give the same output. No DB access. |
| `app/agents/` | Five judgement components: Research, Opportunity (screener), Fee, Allocation, Adversary | See [AGENTS.md](AGENTS.md). |
| `app/providers/` | Market-data and FX providers behind a resilience tier | `builtin` is synthetic. Never present it as market data. |
| `app/worker/` | `scheduler.py` (APScheduler). Celery was deleted in PR 112 | |
| `app/schemas/` | Pydantic models, including the original lifecycle state machine | |
| `app/static_app/` | The whole UI: one `index.html` (~4,100 lines), `sw.js`, manifest, icons | See [FRONTEND.md](FRONTEND.md). |
| `tests/` | ~2,290 tests in 254 files | See [DEVELOPMENT.md](DEVELOPMENT.md). |
| `scripts/` | PowerShell operator tooling: smoke tests, ops checks, book-editing CLIs | See [scripts/README.md](../scripts/README.md). |

## 3. Boot sequence (`app/main.py:38-218`)

1. Configure JSON logging and load settings. (No Sentry: deleted in PR 116, D81.)
2. **Lifespan:**
   1. If `AUTO_CREATE_TABLES` (default **False**; tests and docker-compose turn it on): `create_all`, plus a hand-kept list of idempotent `ALTER TABLE … IF NOT EXISTS` statements on Postgres. In production the schema comes only from Alembic.
   2. In production, boot logs an ERROR if `AUTO_CREATE_TABLES` is on.
   3. If `ENABLE_SCHEDULER` (default True): `start_scheduler()`, then `asyncio.create_task(warm_loop())`. Note that `start_scheduler` makes some network calls synchronously on the boot path (a market refresh and 8 Yahoo futures fetches).
3. **Middleware** (outermost first): `_audit` → `_security_headers` → `_observe` (request id + Prometheus) → CORS.
   - `_audit` writes one `audit_log` row per write request.
4. Mount 32 routers, `/dashboard` (redirect), `/app` (static, `Cache-Control: no-cache` on `*.html` and `sw.js`) and `/` (service info JSON).

## 4. Authentication and authorisation

- **Browser:** Google OAuth (`/login` → `/auth/google/*`). The callback checks the email against
  `ALLOWED_EMAILS` and sets an httpOnly `iw_session` cookie holding a 30-day SUPERADMIN JWT.
  JS never touches a token. Any 401 sends the page to `/login`.
- **Logout ends this browser's session** (PR 111, D73). `/auth/logout` writes the cookie's (and any bearer's) `jti`
  to `revoked_tokens`, and `require_role` refuses a revoked `jti`. The revoked set is held in memory and reloaded from
  the table at boot (PR 111.1), so there is no DB read in the auth path; safe because the app is one process. Other devices keep their sessions. To end every session at once, rotate `JWT_SECRET`.
- **Scripts:** the `X-Agent-Key` header equal to `AGENT_API_KEY` grants SUPERADMIN. Every smoke script uses this.
  See [OPERATIONS.md §5](OPERATIONS.md#5-secrets-and-rotation).
- **Signing:** HS256 with `JWT_SECRET` if set, which keeps sessions alive across redeploys. Otherwise
  RS256 with an ephemeral key, and every restart logs everyone out.
- **`REQUIRE_AUTH` defaults to False** (open demo mode, everyone is SUPERADMIN). Production sets it to true.
- Roles `SUPERADMIN > ADVISOR > ANALYST > READ_ONLY` exist (`app/core/auth.py:23`). **Every write route is ANALYST
  or above** (PR 110). A READ_ONLY token may call reads and the compute-only POSTs (`/ask`, research,
  frontier preview, `/scenario`, `/whatif`). The owner (SUPERADMIN) can do everything (D74). Two tests enforce
  this: `test_every_write_route_is_analyst_or_above` fails on a new READ_ONLY writer, and
  `test_no_new_route_is_public_by_accident` fails on a new unauthenticated route (the PUBLIC allowlist is the
  review). Push subscriptions are scoped to the caller. `/providers/health` and `/adversary/diagnostics` are
  SUPERADMIN-only. `/docs`, `/redoc` and `/openapi.json` are off in production. The dead `API_KEY` setting was
  removed; boot logs an ERROR if `REQUIRE_AUTH` is off in production.
- The static shell `/app` is public. The data endpoints are what's gated.

## 5. Request flow: opening Today

1. `loadToday()` in `index.html` fires six requests **in parallel** (PR 104c.1): `/rules`,
   `/health-check`, `/recommendations`, `/plan`, `/plan/projection`, `/risk/alerts`, plus the NAV chart.
   The service worker never caches `/api/*`: a stale portfolio is worse than a clear failure.
2. `GET /api/v1/recommendations` → `get_session` → `acting_user` (ensures the user, loads the plan,
   `thresholds.use(plan)` sets a ContextVar so this request uses the user's card thresholds).
3. `build_recommendations(session, user)` (`app/services/recommendations.py:1275-3150`) builds the
   whole Today screen. The step-by-step pipeline is in [DOMAIN.md §4](DOMAIN.md#4-the-today-pipeline). In outline:
   - **Snapshot:** positions, NAV in ILS, cash, plan, the book target mix, and a single shared `FundingLedger`.
   - **Generators:** about 25 card sources (concentration, tax-loss, rebalance, core realign, goal levers,
     fees, profit/hedge/momentum, holding review, redeploy cash, commodities, buy ideas, benchmark,
     frontier, war room, triggered rules, advanced rules, strategy signals, sleeve drift).
     - Slow synchronous agents run in threads (`asyncio.to_thread`).
     - Risky ones each run inside their own SAVEPOINT (`_agent_tx`), so one failure can't abort the Postgres transaction.
   - **Reconcile:** prune competing buys, set priority, fold alternatives, check contradictions, convert rule conflicts into decision cards.
   - **Filter:** the server-side mute/done state in `kv_settings` (PR 69: *the server alone decides which cards show*).
   - **Split:** actionable cards (top 12 + "more") vs **Heads-ups** (PR 102).
   - Returns per-stage `timings_ms` and a `degraded` list.
4. **Accept:** `POST /recommendations/{id}/accept` → `apply_recommendation`. It rebuilds the list, finds the card,
   executes its `apply` spec, writes the `transactions` ledger, asserts that every share change is a
   recorded trade and that NAV is conserved, then marks the card done.
5. **Warm-up:** `warm_loop()` (`app/services/warmup.py`) runs `build_recommendations` for every user 20 s after
   boot and every 45 min after that. Reason: the first Today after a deploy took 22 s cold vs 0.9 s warm.

## 6. Market data, FX and LLM

- **Providers** (`app/providers/registry.py`), chosen by settings:
  - `MARKET_DATA_PROVIDER` ∈ {`builtin`, `yahoo`, `fmp`}
  - `FX_PROVIDER` ∈ {`builtin`, `frankfurter`}
  - An unknown value falls back to `builtin`, which is a **deterministic fake** (hash prices, frozen USD/ILS 3.71).
  - Production runs Yahoo + Frankfurter. `/api/v1/data-status` reports the effective source, and the UI shows a
    green "live" or amber "illustrative" banner.
- **Resilience tier** (`app/providers/resilience.py`): a TTL cache with a stale copy, a token bucket,
  a circuit breaker that ignores "no data for this symbol", retry ×3, and **single-flight** (one fetch
  per key; concurrent callers wait).
  - Five tiers: market 15 s, FX, economic, history 1 h (6 h stale), fundamentals 6 h (24 h stale).
  - History requests are bucketed (260/520/1300/2600 days) so short windows reuse long series.
- **Total-return history** (dividend-adjusted, PR 101) is used for every backtest. It falls back to price-only and says so.
- **Bypasses** (documented debt): the futures snapshot (`markets_service`) and the secondary Yahoo quote
  fallback in `pricing_service` call Yahoo directly.
- **Blocking I/O is offloaded:** providers are synchronous urllib, called via `asyncio.to_thread` so the event loop stays free (PR 50).
- **No economic-events source exists.** The Research Agent returns nothing, and the UI says so (PR 58).
- **LLM:** Google Gemini over REST (`app/services/llm.py`, `gemini-2.0-flash`), on only when
  `GOOGLE_API_KEY`/`GEMINI_API_KEY` is set. Used for:
  - Ask InvestWise (grounded Q&A)
  - AI summaries
  - holding research
  - war-room narration
  - the optional adversary narrative

  The advisor **never invents numbers** and always ends with "Not financial advice."
  ([DOMAIN.md §9](DOMAIN.md#9-the-advisor-llm)).

## 7. Background work

APScheduler `BackgroundScheduler` (`app/worker/scheduler.py`) runs in-process. Each job runs in
a thread, wrapped by `_guarded`, which records runs/failures/last error (and `previous_run`) in
`JOB_STATE`, exposed at `/api/v1/push/status`. Since PR 143 every outcome is also written to `kv_settings`
(`job_state:<id>`) and read back when the scheduler starts, so a deploy no longer erases last night's
record. The rules are checked only inside `price_refresh`: a failing check fails that job with the
reason (one bad book no longer skips the rest), and Today shows a HIGH heads-up when the last check
failed or none has succeeded for 75 minutes (`_rule_check_headsup`). Since PR 144 a `boot_check` (`app/worker/self_check.py`) resolves every import a job makes only when it runs, each job is `registered_at` boot, and `_job_health_headsup` puts one HIGH heads-up on Today for any job failing or not succeeding in 26 h; both health heads-ups are pushed. Times are container time (UTC on Railway).

| Job | Schedule | What it does |
|---|---|---|
| `price_refresh` | every 30 min (primed at boot) | Reprice every position (primary → Yahoo fallback, minor-unit/currency repair), then evaluate trading rules |
| `futures_warm` | every 5 min (primed at boot) | Refresh the strategies' regime cache (`regime_service`, hourly) that Today reads, then the futures cross-check snapshot; a futures outage keeps the last snapshot, marked stale, and fails the job |
| `push_evaluate` | every 30 min (5 min after the price refresh) | Per subscriber: push every new card (by id, D140), app-health heads-ups, risk alerts, price moves |
| `standing_orders` | every 60 min | Credit due monthly deposits (contribution + cash), idempotent |
| `push_digest` | 07:00 daily | Daily digest push |
| `nav_snapshot` | 22:10 (trading days) | Record real NAV (`nav_snapshots`) + health snapshot |
| `backtest_refresh` | 03:30 | Recompute `strategy_backtests` (10 y total-return history) |
| `solver_refresh` | 04:10 | Re-solve stored targets, core search and the **frontier/goal** (`solver_verdicts`) |
| `holding_review` | 04:40 | Backtest each holding's sell signals → `kv_settings holding_review:*` |
| `strategy_signals` | 06:15 | Evaluate rule-based sleeves → `strategy_signal_state` flips → card + push |

**Cross-loop rule** (from incident `5a6dfef`): every job calls `asyncio.run()` in its own thread, so it
**must build its own engine** in that loop and never borrow `app.core.database.engine`.
asyncpg rejects a connection from another loop; SQLite doesn't, which is why only the Postgres CI job
catches it. The same rule applies to test fixtures next to `TestClient`; use the `own_session` fixture there.
All 9 DB jobs comply (audited 2026-10-08). Five use NullPool (nav, solver, holding review, standing orders, warm-up via
injection). Four use a pooled engine created and disposed inside their own `asyncio.run` (pricing, push, backtest, strategy
signals). Both are loop-safe; use NullPool for new jobs.

**A job that fails says so (PR 107, D29).** No job catches its own failure: it raises, and `_guarded` records
`last_ok: false` with the error. A fan-out (push per subscriber, NAV per user) carries on past one bad item, counts it, and
raises at the end. `tests/test_every_job_says_when_it_fails.py` captures every `add_job` from `start_scheduler()` and
requires a healthy run and an injected failure for each, so a new job without a failure test fails CI.
`price_refresh`, `futures_warm` and `standing_orders` are primed with `next_run_time=now`, never
called on the startup thread, so boot never waits on a provider. A futures outage keeps the last snapshot (marked
`stale`) instead of caching an empty one.

There is no Celery: it ran eagerly in-process behind three demo `/jobs/*` routes and was deleted in PR 112,
with the `celery`/`redis` dependencies. The hourly `market_refresh` job went too (it scanned and discarded).

## 8. State that is not in an obvious table

A lot of real application state lives in the generic `kv_settings` key/value table. Know these keys
before debugging Today:

| Key | Holds |
|---|---|
| `dismissed_recs:{email}` / `completed_recs:{email}` | Today's Ignore (7-day mute) and Done maps, with each card's rendered state |
| `headsups_dismissed:{email}` | Dismissed heads-ups (permanent) |
| `holding_review:{ticker}` | Nightly sell-signal measurement per holding |
| `buyback_holds:{email}` | Cash a buy-back hold keeps reserved |
| `basis_baseline:{email}` | Accepted residual cost-basis baseline (reconcile) |
| `vapid_public_key` / `vapid_private_key` | Web-push keypair (see [OPERATIONS.md](OPERATIONS.md): pin it in env) |
| `last_price_refresh` / `last_price_source` | Price freshness / effective source |
| `pxbase:{subject}:{ticker}` | Price-move push baselines |
| `push_cards_baseline:{subject}` | When PR 156's every-card rule first ran for this subscriber (cards already on Today were marked, not pushed) |
| `caps_to_bands_done:*`, `plan_sleeves_backfilled_v1`, `plan_core_backfilled_v1` | One-shot migration flags |
| `job_state:{job id}` | Each scheduled job's last outcome (PR 143), reloaded at boot |

In-process caches: the war-room payload (10 min per book, `WAR_ROOM_CACHE_TTL_SEC`; tests set 0), the
futures snapshot (5 min) and the provider tiers above. All of them are lost on restart (`JOB_STATE` is
not: PR 143 persists it).

## 9. Legacy subsystems (being removed)

The day-one spec built a typed lifecycle (`Detected → Vetted → Optimized → Ranked → Displayed`, with `Vetoed` as
terminal) in `app/schemas/state_machine.py`. It was persisted via a pipeline into `decision_feeds`/`decision_items`,
with a learning loop on `user_actions`. None of it drove the Today screen. Deletion runs in three stages (D76):
- **PR 112:** the standalone calculator routes, Celery, the broker scaffold and the no-op `market_refresh` job.
- **PR 113:** the decision-feed cluster: `pipeline.py`, `AllocationAgent`, the safety, XAI and learning engines, the
  `/decision-feed/*`, `/actions`, `/learning/profile` and `/tax/review` routes, and the feed/XAI schemas.
- **PR 114:** the tables.

**Kept on purpose:** `engines/state_machine.py` and the lag, decision, risk and tax engines. The war room and
What-if run on them.

## 10. Where to look first

| You want to… | Start at |
|---|---|
| Understand a Today card | `app/services/recommendations.py` (`build_recommendations`, `apply_recommendation`) + [DOMAIN.md](DOMAIN.md) |
| Change how buys are funded | `app/services/funding_service.py` (`propose_funded_buy`) |
| Change the goal / odds | `app/services/goal_service.py`, `frontier_service.py`, `frontier_inputs.py`, `app/engines/frontier.py`, `paths.py` |
| Add a setting | `plans` table → migration (ships alone) → `plan_service` → `thresholds.py` if it's a card threshold → a sheet in `index.html` |
| Add a scheduled job | `app/worker/scheduler.py` + its own NullPool engine + **a test that runs it** |
| Debug prices | `app/services/pricing_service.py`, `app/providers/registry.py`, `/api/v1/data-status`, `/api/v1/providers/health` |
| Debug a 500 in prod | `scripts/get-error-log.ps1`, the `X-Request-ID` header, [OPERATIONS.md](OPERATIONS.md) |

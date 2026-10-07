# Data model

> The tables in `app/models/tables.py` (29) and the 28 Alembic migrations that build them.
> Written 2026-10-07 at PR 104d.1. See [ARCHITECTURE.md §8](ARCHITECTURE.md#8-state-that-is-not-in-an-obvious-table) for
> state kept in `kv_settings`.

## Conventions

- Every model except `KVSetting` has a UUID `id` (`PKMixin`, `app/models/base.py:13`) and
  `created_at`/`updated_at`.
- **Money columns are `MONEY = Numeric(18,4)`** (`tables.py:30`). Write them through the `money()` quantiser
  (`tables.py:36`, 4 dp HALF_EVEN), because Postgres rounds and SQLite doesn't. (A *different* `money()` in
  `app/core/money.py` rounds to 2 dp HALF_UP for display. Don't mix them up.)
- **ILS is the base currency.** Positions carry their native price and currency (`meta.currency`), and every
  valuation converts to ILS through the FX provider. Two quirks: `ILA` (agorot) is 1/100 ILS, and LSE pence are 1/100 GBP.
- `JSONB_OR_JSON` is JSONB on Postgres and JSON on SQLite.
- The newer tables are keyed by **`subject` = the user's email string** (no FK). The older ones use `user_id`.
- **Cash is a position row** (ticker `CASH`, price 1.0, ILS); `intake_service.is_cash_position` identifies it.
  It must never be quoted as a listed stock (`CASH` is also a real NASDAQ ticker; this has caused several incidents).

## Entity map

```
users ─┬─ entities ── accounts ── positions ──< transactions (position_id SET NULL)
       │                └─ buckets (unused)
       ├─ plans (1:1) ─── thresholds JSON, goal limits, cash rules, Kelly/confidence dials
       ├─ decision_feeds ── decision_items ──< user_actions        (legacy pipeline)
       └─ broker_connections                                       (mock only)

by subject (email):
  plan_sleeves · plan_applications · solver_verdicts · strategy_signal_state
  trading_rules ──< rule_events · contributions · standing_orders · nav_snapshots
  push_subscriptions · notified_events

global: strategy_backtests · credentials · revoked_tokens · audit_log · kv_settings · whs_snapshots · tax_profiles (unused)
```

## Tables

### The book

| Table | Key columns | What it is |
|---|---|---|
| `users` | email (unique), name, role, tax_year | One row per login. |
| `entities` | user_id, name, entity_type (Personal / Spouse / Corp) | Ownership buckets. In practice one: "Personal". |
| `accounts` | entity_id, name, broker, currency | Holds positions. |
| `positions` | account_id, ticker, market (TASE / NYSE / NASDAQ / SPOT…), quantity, cost_basis, current_price, meta JSON | **The tracked book.** `meta` carries asset_class, currency, volatility, expense ratio, etc. Cash is a row. |
| `transactions` | subject, ticker, market, txn_type (Buy / Sell / Adjust / Void), quantity, price, fees, value_ils, tax_ils, source, note, executed_at | **The trade ledger.** Every book change made by an Accept writes legs here (PR 3, PR 73). A test-time hook fails any quantity change without a matching row. History repairs are preview-first. |
| `contributions` | subject, amount_ils (signed), kind, occurred_at | External money only: deposits/withdrawals. Drives "you put in", gain %, the goal, and time-weighted return flows. |
| `standing_orders` | subject, amount_ils, day_of_month (1–28), active, next_run_on | Monthly deposits. The hourly job writes a contribution and credits cash, idempotently. The app does not move money; the user sets the same transfer up at the bank. |
| `nav_snapshots` | subject, as_of (date, unique per subject), nav_ils, cash_ils, invested_ils, positions, source | **The only real NAV history** (from 2026-08-16; earlier NAV is unrecoverable). Written at 22:10 on trading days or by "Record today now". |

### The plan

| Table | Key columns | What it is |
|---|---|---|
| `plans` | user_id (1:1) and many setting columns, listed below | The user's goal and every decision they control. |
| `plan_sleeves` | subject, strategy_id, sleeve_pct | Strategy sleeves. The **core** is the implicit remainder (its strategy is chosen via `/plan/core`). |
| `plan_applications` | subject, action (apply / undo), before_state, after_state | Audit of sleeve-size changes from the target solver. No trades. |
| `solver_verdicts` | subject, solver_version, outcome, detail JSON, source (on_demand / scheduled) | **Multiplexed:** target-solver verdicts, core-search verdicts, and the **frontier/goal solve** (`FRONTIER_TAG`). `GET /plan/frontier` reads the latest. |
| `strategy_backtests` | strategy_id (unique), metrics, robustness, data_source, benchmark_ticker, period, last_error | Nightly measured strategy results. `/strategies` only reads them. |
| `strategy_signal_state` | subject, strategy_id, target {ticker: weight}, flipped_at, previous_target, notified | Last signal per rule-based sleeve. A flip raises a card and a push. |

`plans` columns grouped by purpose:

| Group | Columns | Migration |
|---|---|---|
| Objective | `objective` (Grow / Balanced / Income / Preserve), `risk_tolerance`, `horizon_years`, `preferred_depth` (flavour) | 0003, 0005 |
| Goal | `target_roi_pct`/`target_roi_period` (ROI on money put in), `target_date`, `target_amount` (legacy), `target_yield_pct`/`target_yield_period` | 0003, 0004 |
| Cash rules | `cash_target_pct` (legacy), `cash_target_ils` (invest down to), `cash_redeploy_at_ils` (card fires above) | 0021, 0024 |
| Frontier dials | `kelly_fraction` (ceiling), `return_confidence` (shrunk / blended / measured; default blended) | 0026 |
| Goal limits | `halve_limit_pct` (crash-risk limit; NULL = 1%), `on_track_pct` (on-track bar; NULL = 70%) | 0027 |
| Card thresholds | `thresholds` JSON, sparse overrides of the defaults in `app/services/thresholds.py` | 0028 |

### Rules

| Table | Key columns | What it is |
|---|---|---|
| `trading_rules` | subject, ticker (`__BOOK__` = whole book), rule_type, level, mode, strategy_id, peak_price, trim_fraction, params JSON, active, triggered | User rules. Types: stop_loss, take_profit, trailing_stop, price_above / price_below, buy_dip, max_weight, strategy_signal, and the advanced rebalance band, profit-protect, trend-break and drawdown guard. **Firing never trades by itself**: it raises a card. |
| `rule_events` | subject, rule_id, outcome (triggered → executed / dismissed / expired), action JSON, notified | The life story of every firing (the Rule history list). |

### Push, auth, ops

| Table | What it is |
|---|---|
| `push_subscriptions` | Web Push devices (endpoint, keys). |
| `notified_events` | Push dedupe signatures. |
| `credentials`, `revoked_tokens` | Password login and refresh-token rotation. Password login is off unless `AUTH_PASSWORD` is set. |
| `audit_log` | One row per write request (method, route, IP, role, sha256 of the payload). Read via `GET /api/v1/audit`. |
| `kv_settings` | Generic key/value store. Holds real app state; see [ARCHITECTURE.md §8](ARCHITECTURE.md#8-state-that-is-not-in-an-obvious-table). |
| `whs_snapshots` | Nightly wealth-health snapshot. |

### Legacy / unused

`decision_feeds`, `decision_items`, `user_actions` (the spec-era pipeline and learning loop; not used by Today),
`broker_connections` (mock only), `buckets`, `tax_profiles` (no readers).

## Migrations

`alembic/versions/`, linear. Production runs `alembic upgrade head && alembic current` as Railway's
**preDeployCommand**. A failed migration fails the deploy and the old container keeps serving.

| Rev | Adds | | Rev | Adds |
|---|---|---|---|---|
| 0001 | `create_all` of the models **as they are now** | | 0015 | `strategy_backtests.benchmark_ticker` |
| 0002 | credentials, revoked_tokens | | 0016 | nav_snapshots |
| 0003 | plans | | 0017 | plan_applications |
| 0004 | plan ROI / yield targets | | 0018 | transaction ledger columns, FK SET NULL |
| 0005 | plans.preferred_depth | | 0019 | drop plans.strategy / strategy_sleeve_pct |
| 0006 | broker_connections | | 0020 | solver_verdicts |
| 0007 | plans.strategy (dropped in 0019) | | 0021 | plans.cash_target_pct |
| 0008 | contributions | | 0022 | trading_rules.trim_fraction |
| 0009 | strategy_backtests | | 0023 | standing_orders |
| 0010 | strategy_signal_state | | 0024 | plans.cash_target_ils, cash_redeploy_at_ils |
| 0011 | backtest last_error | | 0025 | trading_rules.params |
| 0012 | plans.strategy_sleeve_pct (dropped) | | 0026 | plans.kelly_fraction, return_confidence |
| 0013 | trading_rules.strategy_id | | 0027 | plans.halve_limit_pct, on_track_pct |
| 0014 | plan_sleeves | | 0028 | plans.thresholds |

Because 0001 runs `create_all` from today's models, the chain is **not a faithful history**: every later migration
must be *guarded* (inspect before add). `tests/test_the_migration_chain_runs_from_nothing.py` proves the chain
still builds from an empty DB. How to write a new one: [DEVELOPMENT.md §6](DEVELOPMENT.md#6-database-migrations).

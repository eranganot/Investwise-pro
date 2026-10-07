# Backlog

> **The single list of open work.** Built 2026-10-07 from the old STATUS.md backlog blocks, execution plans V7/V8/v2.2,
> and a full code review done for the docs overhaul. It replaces the "Next", "Backlog" and "Ops actions" tables that
> used to sit at the top of STATUS.md (archived verbatim in `archive/status/STATUS-HISTORY-to-PR87.md`, with their evidence).
>
> **Status of plans:** execution plan v2.2 (`archive/plans/EXECUTION_PLAN_2026-10-05_cards-truth-and-plan-tab.md`) is
> **complete**, and so is the frontier plan. **No successor plan exists yet.** Write one by re-validating the rows
> below against production first (D35).
>
> Before working an item: confirm it is still open (several older items turned out to be closed). When it ships, delete
> the row, note the PR, and add a STATUS entry.
>
> Priority: **P1** do next · **P2** soon · **P3** when convenient · **Parked** = waiting on a decision · **?** = owner to decide.

---

## Ops (owner-only)

| # | Item | Status | Pri |
|---|---|---|---|
| O1 | **Pin the VAPID keypair in Railway.** A DB rebuild would silently kill push. | Sources disagree: V7 (09-06) says done, while the STATUS ops table (09-28) said `vapid_pinned_by_env: false`. **Check `/api/v1/push/status` live.** Runbook: [OPERATIONS.md §6](OPERATIONS.md#6-ops-runbook-owner-only) | P1 |
| O2 | **Rotate the GitHub PAT and the Railway token** (one project-scoped token). Never rotated since first flagged on 2026-08-03. | Open | P1 |
| O3 | **Database backups and a restore procedure.** Nothing in the repo describes them. | Open, newly identified 10-07 | P1 |
| O4 | Install the `pre-push` hook on every machine (the branch-protection substitute) | Check with `ops-check.ps1` | P2 |
| O5 | Rotate the Postgres password | Parked deliberately: only for a reason (D40) | Parked |

## Product (owner decisions)

| # | Item | Source | Pri |
|---|---|---|---|
| P1 | **Review the goal definition.** Should deposits or extra years raise the "30% on every shekel" target? Plan v2.2 promised this backlog entry but it never reached STATUS. | plan v2.2, PR 99 "Decided 2026-10-05" | ? |
| P2 | **Align the two minimum-trade numbers:** ₪100 card minimum (`min_card_trade_ils`) vs ₪250 funded-leg minimum (`min_funded_trade_ils`). Both are settings now, but not aligned. | STATUS PR 104c/104d | ? |
| P3 | The goal model doesn't simulate stops on BND/GOOGL/VTI/UGA/USO (only sleeve tickers + the guard). It is disclosed in the UI but not modelled. | STATUS PR 104b/104c.1 | P2 |
| P4 | Remaining hard-coded decisions → settings ("every decision is a setting"): `RISK_CAPS`, `CASH_FLOOR_PCT`, `OBJ_TARGET`, the 5-pt sleeve drift band, mute/done TTLs and re-fire deltas, near-rule/near-cap constants, card-priority weights, frontier draws/bands, Kelly-by-objective. | Code review 10-07 | P2 (owner to pick which) |
| P5 | Broker integration (real orders). | `archive/plans/BROKER_INTEGRATION_PLAN.md` | Parked (D41) |
| P6 | Israeli index funds on TASE. | Provider can't price numeric symbols | Parked |
| P7 | B1: what `/plan` shows for volatility (store in-market vol nightly; match windows). | STATUS backlog B1 | Parked |
| P8 | B-margin-A (cash research: any name may replace a top-up), B-sells-trend (sell on trend alone), B-review-ratio (revisit the measured-edge bar). | STATUS backlog | Parked |
| P9 | V8 P3: per-holding day attribution on the NAV card; intraday "today" point on the chart. | `archive/plans/EXECUTION_PLAN_2026-09-14_v8.md` | ? |
| P10 | Promote engines to agents ("Tier 4") + a synthesising "Chief" agent (decided 2026-08-30, not started). Or drop it. | [AGENTS.md](AGENTS.md) | ? |
| P11 | The objective tilt for the screener (`OBJECTIVE_WEIGHTS` is empty and `OWNER_REVIEWED = False`). | AGENTS.md | ? |

## Correctness (found in code)

| # | Item | Where | Pri |
|---|---|---|---|
| C1 | **Sleeve funding and the target solver's cost preview call `plan_funding` without `book_target`**, so overweight is measured against the objective table (the PR 42 defect class). The AST guard only scans two files. | `strategy_service.py` ~780/891/995, `target_solver.py` ~603, `tests/test_every_funding_call_passes_the_book_target.py` `MODULES` | P1 |
| C2 | Multi-leg sleeve funding and `redeploy_cash` skip `propose_funded_buy`, so there is no post-trade mix simulation or drift check. CLAUDE.md's "every funded buy" claim is not literally true. | `strategy_service`, RC `_plan_weight_pass` | P2 |
| C3 | **ILS leak:** the assistant/digest context and the backtest-validation input value holdings as `qty × price` in native currency. | `context_service.py` ~59, RC ~2927 | P1 |
| C4 | Four scheduled jobs swallow exceptions and record success: `backtest_refresh`, `strategy_signals`, `push_evaluate`, `push_digest`. | `backtest_service`, `strategy_signal_service`, `push_service` | P1 |
| C5 | Five scheduled jobs have no test that runs them by name (the four above + `futures_warm`). This violates D29. Verify they aren't covered indirectly, then add tests. | `tests/` | P1 |
| C6 | `POST /broker/sync` with the mock default writes three fake holdings into the real book (READ_ONLY suffices). | `broker_sync.py` | P1 (gate or delete) |
| C7 | `/decision-feed/weekly` silently falls back to demo observations. | `workflows.py` ~162 | P2 (delete with L1) |
| C8 | The `/ask` "never invent numbers / Not financial advice" rule is prompt-only: no output verification and no test asserting the suffix. | `ask_service.py` | P2 |
| C9 | GET `/recommendations` has write side effects (releases holds, resolves rules/signals). The warm loop and assistant therefore mutate state. Document it, or move the writes to a job. | RC | P3 |
| C10 | Two regimes: futures `cached_regime()` drives Today's risk-off note, while `engines/regime.py` says futures are never an input. Pick one. | RC ~2167, `markets_service` | P3 |
| C11 | V8 P0: market day-change uses `chartPreviousClose` (crude +5.5% vs +1.28%). Verify at HEAD. | `markets_service` | P2 (verify) |
| C12 | `broker_sync.py:33` raw price fallback; `intake_service` whole-dict `meta` assignments. | V7 | P3 |
| C13 | The unspent-leg refund (`unspent_ils`) has no test. | V7 | P3 |
| C14 | Israeli CGT inflation-indexed basis is not modelled (disclosed only); needs accountant sign-off. | `tax_engine` | P3 |

## Security posture

| # | Item | Pri |
|---|---|---|
| S1 | Many write endpoints require only READ_ONLY (`acting_user`), while others require ANALYST. Make it consistent. | P3 (single user) |
| S2 | Logout doesn't revoke the 30-day session JWT; `require_role` never checks `revoked_tokens`. | P3 |
| S3 | `POST /push/unsubscribe` deletes any endpoint, not scoped to the caller. | P3 |
| S4 | `/metrics`, `/docs`, `/openapi.json`, `/providers/health`, `/markets/*` are unauthenticated. | P3 |
| S5 | `API_KEY` / `require_api_key` is dead code, so the production warning "write endpoints are unauthenticated" is misleading. Delete it. | P3 |
| S6 | No rate limiting on the app's own endpoints. | P3 (posture decision) |

## Cleanup and tech debt

| # | Item | Pri |
|---|---|---|
| L1 | **Delete the legacy pipeline** or mark it legacy: `pipeline.py`, `AllocationAgent`, `SafetyEngine`, XAI, learning, `decision_feeds/items`, `user_actions`, the standalone calculator routes, Celery `/jobs` (+ the `celery`/`redis` deps), `market_refresh` job, `buckets`, `tax_profiles`. See [API.md § Legacy](API.md#legacy--not-used-by-the-ui). | P2 |
| L2 | **`investwise.db`** (tracked SQLite, unused; `*.db` is gitignored) → `git rm --cached`. **`frontend/`** (only a stale `package-lock.json`) → delete; also change the `FRONTEND_ORIGIN` default (`localhost:5173`). *Left for the owner in the 10-07 overhaul.* | P2 |
| L3 | `google-genai` pinned but never imported; `sentry-sdk` imported but not pinned. | P3 |
| L4 | Naming debt: `cash_floor_pct()` / `cash_floor_ils()` return the soft target, while the setting `cash_floor_ils` is the hard floor; `MIN_TRADE_ILS` exists in two modules with two values; `signal_service` "listing price" = 50-day SMA; `rebalance_to_objective` targets the book target. | P3 |
| L5 | Stale comments: `tables.py:1` ("All 11 tables"), the `NavSnapshot` docstring ("transactions never written"), `buyback_hold.py:29` (kv_settings), `PlanSleeve.is_core` ("nothing reads"), `test_startup_ddl_covers_migrations.py` docstring, conftest :61, `market_state.py`, the `frontier.py` pipeline docstring. | P3 |
| L6 | Stale UI copy: Edit-goal says "change them on the Cash tab" (no such tab); `goalSummary` empty state points at a removed "Goal settings" form; the commodity input says "$ amount". | P2 (small PR) |
| L7 | Fat route modules: `routes/plan.py` (1,123 lines) and `routes/intake.py` (944) hold business logic. | P3 |
| L8 | Floats → Decimal across money paths (553 `float(` vs 38 `Decimal(` at 09-06). All or nothing. | P3 |
| L9 | Two `money()` helpers with different precision/rounding. | P3 |
| L10 | Bypasses of the provider resilience tier: futures fetch, secondary Yahoo quote fallback. | P3 |
| L11 | Startup does synchronous network I/O (market refresh + 8 futures fetches) before serving. | P3 |
| L12 | Frontend: no focus styles or sheet focus trap, clickable non-focusable elements, `.seg` defined twice, undefined CSS vars, inline styles in PR 104 UI, native `alert`/`confirm`. | P3 |
| L13 | `candidate_search.py` + its test sit **untracked** in the main checkout (since 2026-08-25, zero callers). Land them or delete them. | P3 |
| L14 | CANE in the commodity screen vs IAU/DBC in the core. | P3 |
| L15 | Surface the Gemini error class in the UI (a billing 429 looked like an outage). | P3 |

## Verification debt

| # | Item |
|---|---|
| V1 | Pixel QA for PR 104a (`qa/QA-2026-10-06-pr104a-plan-tabs.md`). Its successors 104b–104d passed, so it is probably covered; record it or void it. |
| V2 | `qa/QA-2026-07-12-notification-alignment.md` and `qa/QA-2026-08-10-p0-safety.md` were never run. Void them or re-scope. |

## Recently closed (do not re-open)

- B2 overlay retirement: executed in PR 59 (`btm_qqq_hold`).
- `cash_target_pct`: superseded by PR 64/68.1 (₪150 floor, ₪1,500 trigger).
- `alembic upgrade head` from nothing: fixed in PR 46 and tested.
- Agent key + default password rotation: done (PR 26 + 08-31).
- Repo hygiene of root plan files and old scripts: done in the 2026-10-07 docs overhaul.

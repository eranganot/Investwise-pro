# PHASE 12 - persist strategy backtests, precompute nightly (Beat the Market, Phase B).
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase12-backtest-store.ps1
#
# DB: adds `strategy_backtests` via alembic 0009. Run `alembic upgrade head`
#     after the deploy if AUTO_CREATE_TABLES is off in production.
#
# NOTHING USER-VISIBLE CHANGES. The Plan tab is untouched -- the new family is
# served from GET /strategies/backtests until the UI can render it (Phase F).
#
# New nightly job at 03:30. To get numbers immediately instead of waiting:
#     POST /api/v1/strategies/backtests/refresh      (slow: fetches ~10y per ticker)

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/services/backtest_service.py",
    "app/services/strategy_catalog.py",
    "app/api/routes/strategy.py",
    "app/worker/scheduler.py",
    "alembic/versions/0009_strategy_backtests.py",
    "tests/test_backtest_service.py",
    "scripts/deploy/phase12-backtest-store.ps1"
)

Invoke-Suite -Focus @("tests/test_backtest_service.py", "tests/test_strategy_backtest.py",
                      "tests/test_strategy_profile.py", "tests/test_plan_explore.py",
                      "tests/test_jobs.py")

$msg = @(
    "feat(backtest): persist strategy backtests; precompute nightly",
    "",
    "Phase B of Beat the Market. A backtest needs ten years of daily closes",
    "for every ticker a strategy touches, so running it inside /strategies",
    "would hang a page load on a network fan-out and make the strategy list",
    "fail whenever a price provider is down. The job writes, the route reads.",
    "",
    "* strategy_backtests table + alembic 0009. One row per strategy,",
    "  carrying engine_version, data source, the exact date span and the",
    "  observation count -- a figure on a card can always be traced back to",
    "  the run that produced it.",
    "* backtest_service: fetch -> run -> store, plus out-of-sample and a",
    "  parameter sweep so fragility travels with the headline number instead",
    "  of being computed once and forgotten.",
    "* Nightly APScheduler job at 03:30 (one more session cannot move a",
    "  ten-year CAGR) and POST /strategies/backtests/refresh so a deploy does",
    "  not have to wait until 03:30 for its first numbers.",
    "* GET /strategies/backtests serves stored rows. Never computes.",
    "",
    "Three storage rules, all the same idea -- a number must be traceable:",
    "",
    "  1. A run that abstains OVERWRITES the previous row with its reason.",
    "     Leaving the old metrics in place would let a figure that can no",
    "     longer be reproduced keep presenting itself as current.",
    "  2. Bumping engine_version makes every older row stale by definition,",
    "     so results from two different engines are never mixed.",
    "  3. computed_at drives a freshness flag. A stale row is still served --",
    "     an old measurement beats none -- but it is labelled as stale.",
    "",
    "strategy_catalog holds the seven specs. Two structural points, both of",
    "which were wrong in the first cut and cost real accuracy:",
    "",
    "  * ``base`` is the core holding, not cash. Measured on TQQQ 2016-2026 the",
    "    identical dip-buy scored 4.15%/yr against T-bills and 15.64%/yr",
    "    against a QQQ core. A test now fails if any swing strategy is",
    "    configured to sit in cash between setups.",
    "  * Risk overlays gate on the instrument HELD, not on the index. Vol",
    "    targeting a TQQQ sleeve off QQQ's volatility produces a full weight",
    "    every day and silently reproduces buy-and-hold.",
    "",
    "A test also fails if any shipped strategy is built on an overlay listed",
    "in MEASURED_FAILURES, so the drawdown brake cannot reach a card.",
    "",
    "DELIBERATELY NOT WIRED TO THE PLAN TAB. The renderer reads s.basket as",
    "[ticker, weight] pairs, s.risk_tolerance and s.profile, and its buttons",
    "resolve ids against services.strategies -- so adding a fifth goal now",
    "would draw a tab of malformed cards with dead buttons. /strategies is",
    "unchanged apart from an added engine-version field; the family is served",
    "from /strategies/backtests until the UI can render a measured strategy.",
    "Baskets are already emitted as [ticker, weight] pairs so that lands as a",
    "UI change rather than a contract change.",
    "",
    "+11 tests. ruff check app clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head' -ForegroundColor Gray
Write-Host '  2. POST /api/v1/strategies/backtests/refresh   (or wait for 03:30)' -ForegroundColor Gray
Write-Host '  3. GET  /api/v1/strategies/backtests           (measured numbers + freshness)' -ForegroundColor Gray
Write-Host '  Nothing to QA on the phone - the Plan tab is unchanged.' -ForegroundColor DarkGray

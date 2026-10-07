# PHASE 10 - "what you put in" is external money, not the current book.
#            Also lands the strategy backtest engine (Beat the Market, Phase 0+A).
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase10-contributions.ps1
#
# What changes for you after this deploys:
#   * "You put in" stops drifting. It reads the contributions ledger, and only a
#     deposit or a withdrawal moves it.
#   * Existing books keep the old estimate until you record a real figure --
#     POST /api/v1/portfolio/contributions {"amount_ils": 20000, "mode": "set"}
#     (or the Holdings control once the UI lands) -- so nobody sees a false 0.
#   * The backtest engine ships unwired: no route, no UI, no behaviour change.
#
# DB: adds the `contributions` table via alembic 0008. If AUTO_CREATE_TABLES is
# on in production the model creates it too, but run the migration to be sure.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/services/intake_service.py",
    "app/api/routes/intake.py",
    "app/providers/live.py",
    "app/engines/strategy_backtest.py",
    "alembic/versions/0008_contributions.py",
    "tests/test_contributions.py",
    "tests/test_strategy_backtest.py"
)

Invoke-Suite -Focus @("tests/test_contributions.py", "tests/test_strategy_backtest.py",
                      "tests/test_portfolio_totals.py", "tests/test_cash.py",
                      "tests/test_cash_pricing_guard.py", "tests/test_intake.py")

$msg = @(
    "fix(portfolio): `"what you put in`" is external money, not the current book",
    "",
    "Reported live: 20,000 deposited, `"You put in 20,790`" displayed.",
    "",
    "invested_ils summed every position's cost_basis and FX-converted it at",
    "TODAY's rate, so it drifted for three reasons that are not deposits:",
    "",
    "  1. FX revaluation - cost_basis is per-share in the position's own",
    "     currency, so every USD holding's `"amount you put in`" moved with",
    "     USD/ILS, daily.",
    "  2. Sells rewrite basis - accepting a sell deletes the position and",
    "     credits CASH at cost_basis 1.0 on the net-of-CGT proceeds, so",
    "     taking a profit RAISED the figure.",
    "  3. Fee swaps and trims re-stamp basis at the live price.",
    "",
    "Trading rearranges money already inside the account; only a deposit or a",
    "withdrawal changes how much of your own money is in it. New Contribution",
    "ledger (user-scoped by email, signed amounts, 0008_contributions)",
    "becomes the only writer of invested_ils, and gain / gain_pct / the",
    "goal-gap projection now rest on a figure only the user can change.",
    "",
    "Deliberately user-scoped rather than hung off a position: Transaction",
    "rows cascade away on delete, which is exactly how the history was lost.",
    "total_contributed returns None rather than 0 for users who have never",
    "logged a deposit, so the legacy estimate survives instead of a confident",
    "`"0 put in`"; the response carries invested_source so the UI can say which",
    "number it is showing.",
    "",
    "Also lands the strategy backtest engine (Phase 0+A of Beat the Market),",
    "which is not yet wired to any route or UI:",
    "",
    "* providers/live: the Yahoo range ladder stopped at `"5y`", so a caller",
    "  asking for a decade silently got five years. Extended to `"10y`" and",
    "  capped there - with interval=1d, range=max returns MONTHLY bars (QQQ:",
    "  329 rows for 1999-2026 vs 2513 for 10y), which a caller counting days",
    "  would have backtested against as if it were daily.",
    "* engines/strategy_backtest: measures a rule instead of assuming it.",
    "  Six overlays (buy-hold, trend filter, MA cross, Donchian, RSI",
    "  pullback, dual momentum), signals executed one day after the close",
    "  that generated them so there is no lookahead, and costs modelled",
    "  explicitly: 25% Israeli CGT deducted at the realizing sale with losses",
    "  banked, plus dealing spread. Feeding it a leveraged fund's own price",
    "  series means decay and financing are already in the data.",
    "  out_of_sample() and sweep() exist so a curve-fitted rule shows up as",
    "  fragile rather than as one flattering number. Abstains with a typed",
    "  reason instead of filling in a guess.",
    "",
    "Tests: +9 contributions (incl. the regression - selling a position and",
    "crediting cash leaves invested_ils at exactly 20,000), +25 engine",
    "(deterministic, synthetic series, no network). ruff check app clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. alembic upgrade head   (creates the contributions table)' -ForegroundColor Gray
Write-Host '  2. Record your real figure:' -ForegroundColor Gray
Write-Host '     .\scripts\set-contributions.ps1 -Amount 20000' -ForegroundColor Gray
Write-Host '  3. GET /api/v1/portfolio -> invested_ils 20000, invested_source contributions' -ForegroundColor Gray
Write-Host '  4. Confirm it stays at 20000 after a trim or a sell.' -ForegroundColor Gray

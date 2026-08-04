# PHASE 24 - Beat the Market cards get their numbers, and a sleeve control.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase24-sleeve-and-chips.ps1
#
# USER-VISIBLE + PWA. SW iw-v12 -> iw-v13, so hard-refresh on the Pixel.
#
# DB: alembic 0012 adds plans.strategy_sleeve_pct. The startup DDL adds it too,
#     so no manual migration is needed.
#
# WHAT CHANGES FOR YOU:
#   * every Beat the Market card now shows chips like the other families --
#     backtested CAGR, volatility, worst drawdown, vs S&P, trades/yr, tax drag
#   * a slider on each card sets how much of your portfolio it governs
#   * Apply / Load / What-changes all honour that slider
#
# Applying at 20% now means 20% of the book follows the rule and 80% stays in
# the core. Previously it meant 100%.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/models/tables.py",
    "app/main.py",
    "app/services/plan_service.py",
    "app/services/strategy_catalog.py",
    "app/services/strategy_service.py",
    "app/api/routes/plan.py",
    "app/api/routes/strategy.py",
    "app/static_app/index.html",
    "app/static_app/sw.js",
    "alembic/versions/0012_plan_strategy_sleeve.py",
    "tests/test_sleeve.py",
    "scripts/smoke/smoke-all.ps1",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/smoke/smoke-e2e.ps1",
    "scripts/get-error-log.ps1",
    "scripts/deploy/phase24-sleeve-and-chips.ps1"
)

Invoke-Suite -Focus @("tests/test_sleeve.py", "tests/test_backtest_service.py",
                      "tests/test_strategy_signals.py", "tests/test_strategy_profile.py",
                      "tests/test_plan_explore.py", "tests/test_shell_cache_headers.py")

$msg = @(
    "feat(plan): measured chips on Beat the Market, and a sleeve you can set",
    "",
    "Two gaps, both reported from the live app.",
    "",
    "1. THE CARDS HAD NO NUMBERS. The chip renderer reads ``profile``, which is",
    "   derived from a lookup table -- and a rule has no derivable profile, so",
    "   the fifth family rendered a name, a description and nothing else while",
    "   Classic 60/40 next to it showed six chips. New measuredProfile() uses",
    "   the same chip vocabulary from the backtest instead: CAGR, volatility,",
    "   worst drawdown, vs S&P, trades/yr, tax drag, and the out-of-sample",
    "   verdict. Every label says measured rather than estimated, because",
    "   those are not the same kind of claim, and each card states its period,",
    "   session count and that a backtest is one path through one sample of",
    "   history.",
    "",
    "2. THERE WAS NO WAY TO SAY HOW MUCH. Applying `"trend-filtered 3x Nasdaq`"",
    "   put the ENTIRE book into TQQQ, because the model basket reads as 100%",
    "   TQQQ in isolation. Nobody runs a leveraged strategy that way. New",
    "   plans.strategy_sleeve_pct (alembic 0012 + the startup DDL, since",
    "   reaching Railway's Postgres with alembic from a laptop has failed",
    "   twice) and a slider on each card, defaulting to that strategy's",
    "   suggested size.",
    "",
    "sleeve_basket() splits the allocation: at 20 percent, a fifth follows",
    "the rule and the rest sits in the core holding. Preview, Apply and Load",
    "all carry the number -- a slider the buttons ignore would be worse than",
    "no slider. Omitting it falls back to the catalog suggestion, never to",
    "100: an entire portfolio in a 3x fund is not a sane default for `"the",
    "user did not say`". A strategy with no core (the unleveraged families) is",
    "unaffected, since there the whole allocation IS the strategy.",
    "",
    "Also exposes strategy_sleeve_pct on GET /plan. Serialising the strategy",
    "id without its size would repeat the bug fixed one commit ago: a field",
    "the app stores, writes and acts on that no caller could read back.",
    "",
    "+7 tests. SW iw-v12 -> iw-v13."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'On the Pixel (hard-refresh first, SW iw-v13):' -ForegroundColor Cyan
Write-Host '  Plan -> Beat the Market -> each card shows chips + a sleeve slider' -ForegroundColor Gray
Write-Host '  Move the slider, tap "What changes?" - the trades should scale with it' -ForegroundColor Gray
Write-Host ''
Write-Host 'Then:  .\scripts\smoke\smoke-e2e.ps1' -ForegroundColor Gray

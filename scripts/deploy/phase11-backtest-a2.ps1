# PHASE 11 - backtest overlays run on a core holding, not on cash (Beat the Market, A2).
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase11-backtest-a2.ps1
#
# Nothing user-visible changes. The engine is still not wired to a route or the
# UI - this is capability for Phase B (persistence + nightly precompute).
#
# No migration, no new table, no scheduler change. Safe to deploy any time.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/engines/strategy_backtest.py",
    "tests/test_strategy_backtest.py",
    "scripts/set-contributions.ps1",
    "scripts/deploy/phase10-contributions.ps1",
    "scripts/deploy/phase11-backtest-a2.ps1"
)

Invoke-Suite -Focus @("tests/test_strategy_backtest.py")

$msg = @(
    "feat(backtest): overlays run on a core holding, not on cash",
    "",
    "Reported: swing is a days-to-weeks game and should not be judged as a",
    "ten-year always-on allocation.",
    "",
    "Correct, and it was doing all the damage. When a setup was not live the",
    "sleeve fell back to T-bills -- but a swing setup is only live 10-15% of",
    "the time, so the measurement was a savings account with a strategy",
    "attached. A swing rule is an OVERLAY: it chooses between the aggressive",
    "instrument and the core holding, not between the instrument and being",
    "out of the market. New ``base`` on the spec is that core; ``risk_off``",
    "survives for strategies that genuinely should sit in cash.",
    "",
    "Same rule, same entries and exits, only the base changed",
    "(TQQQ dip-buy, 2016-2026, net of 25% CGT):",
    "",
    "    base = BIL     4.15%/yr   -9.21 vs SPY",
    "    base = QQQ    15.64%/yr   +2.28 vs SPY",
    "",
    "PER-TRADE STATISTICS. CAGR alone cannot judge a strategy that is out of",
    "the market most of the time: expectancy, profit factor, average holding",
    "period and time-in-market separate `"the rule is bad`" from `"the rule is",
    "good and barely deployed`". The dip-buy turns out to be the latter --",
    "+1.12% per trade, profit factor 1.84, 11-day holds, in the market 15.5%",
    "of the time. That is a sizing problem, not a signal problem.",
    "",
    "NEW OVERLAYS.",
    "* vol_target -- scale exposure so realized risk stays roughly constant.",
    "  Decay scales with variance, so this attacks the decay term itself",
    "  rather than trying to time direction. TQQQ 2016-2026: 25.99%/yr with a",
    "  56.2% drawdown against 39.17% and 81.8% simply held -- 25 points of",
    "  drawdown removed for 13 of return. Gate it on the instrument being",
    "  held, not on the index: gating on QQQ while holding TQQQ silently",
    "  produced a 1.0 weight every day and reproduced buy-and-hold.",
    "* rebalance_band -- recomputing the weight daily traded 342 times a",
    "  year, which at 25% CGT is a strategy whose costs eat its own edge.",
    "  A band cuts that to ~164 with the CAGR slightly UP, because the tax",
    "  drag falls from 9.45 to 8.89 points.",
    "* sector_momentum -- top-N of a universe on trailing return, with an",
    "  absolute-momentum veto. Measured at 7.27%/yr, worse than SPY.",
    "* drawdown_brake -- KEPT BUT MARKED BROKEN. It capped nothing (83.7%",
    "  drawdown vs 81.8% held) and split 58.8% in-sample against 3.2% out.",
    "  New MEASURED_FAILURES map, surfaced as ``known_failure`` on every",
    "  result, so a consumer cannot render a strategy built on it without the",
    "  caveat. An engine that quietly serves a rule it has measured as broken",
    "  is worse than one with no rule at all.",
    "",
    "Also commits the two deploy scripts from the contributions fix, which",
    "were living only on my disk -- set-contributions.ps1 is currently the",
    "only way to correct invested_ils on a fresh install.",
    "",
    "+10 tests (36 total in this module), all deterministic on synthetic",
    "series, no network. ruff check app clean. Nothing is wired to a route or",
    "the UI yet; this is engine capability for Phase B."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'Nothing to QA on the phone - no user-visible change.' -ForegroundColor Cyan
Write-Host 'Next: Phase B (persist backtests, nightly precompute, serve from cache).' -ForegroundColor Gray

# PHASE 3 - Wealth health score: remove the invisible ceilings.
#
# "How come I can't be more than 80?" - the number was right. Three hard caps,
# none of them shown: a hardcoded thematic=60 at 15% weight, a tax base of 85,
# and a risk formula that only reached 100 at zero volatility.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase3-health.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/engines/whs_engine.py",
    "app/services/portfolio_analytics.py",
    "app/api/routes/workflows.py",
    "app/services/ai_service.py",
    "tests/test_health_score_ceiling.py"
)

Invoke-Suite -Focus @("tests/test_health_score_ceiling.py")

$msg = @(
    "fix(health): recalculate the wealth score so 100 is actually reachable",
    "",
    "Reported: 'how come I can't be more than 80?'. The 78 on screen was exactly",
    ".25(70) + .25(84) + .20(100) + .15(73) + .15(60) - three invisible caps:",
    "",
    "1. thematic was passed as the constant 60.0 at 15% weight. It has no",
    "   measured input and was never displayed, so it silently removed 6 points",
    "   from every score (a flawless book topped out at 94) and left 15% of the",
    "   number unexplainable. Weighting a constant is inventing a number, which",
    "   is exactly what this codebase is not supposed to do. Dropped from the",
    "   portfolio composite and the remaining four renormalized to",
    "   30/25/25/20 risk/tax/alloc/liq. The standalone /whs endpoint keeps the",
    "   legacy five-component weighting.",
    "",
    "2. tax efficiency started from an arbitrary base of 85, so that component",
    "   could never reach 100 however clean the book was. Now starts at 100 and",
    "   falls with the unharvested-loss ratio: nothing to harvest = 100.",
    "",
    "3. risk was 100 - vol% x 2, which only reaches 100 at zero volatility - the",
    "   app marked you down for being invested at all, contradicting every",
    "   recommendation it made. Risk is now scored against the plan's own",
    "   volatility budget (Low 10% / Medium 15% / High 25%): inside the budget",
    "   scores 85-100, exceeding it costs. A Grow investor is no longer punished",
    "   for the volatility their objective requires.",
    "",
    "/health-check also returns weights, max_achievable, the volatility cap and",
    "realized vol, so the UI can show how the number is built.",
    "",
    "+6 tests (test_health_score_ceiling.py)."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nExpect your score to MOVE UP after this deploy - the components are" -ForegroundColor Cyan
Write-Host "rescaled, so compare the new chips rather than the old total." -ForegroundColor Cyan

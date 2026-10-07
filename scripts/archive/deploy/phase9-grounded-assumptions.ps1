# PHASE 9 - stop defaulting missing metadata to zero; fix the cap bug; one cash card.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase9-grounded-assumptions.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/strategy_profile.py",
    "app/services/portfolio_analytics.py",
    "app/services/recommendations.py",
    "app/api/routes/plan.py",
    "tests/test_redeploy_cash.py",
    "STATUS.md"
)

Invoke-Suite -Focus @("tests/test_redeploy_cash.py", "tests/test_health_score_ceiling.py",
                      "tests/test_strategy_profile.py", "tests/test_plan_explore.py",
                      "tests/test_reconcile.py", "tests/test_portfolio_totals.py")

$msg = @(
    "fix: ground missing holding metadata; clip the cap; one card per pot of cash",
    "",
    "Reported: 'why do you say my ROI is 0% if the tool says 7.79%?'. Confirmed",
    "live - every equity (COW, MSFT, AMZN, V, SCHD) has BOTH expected_return_pct",
    "and volatility_pct blank; only CASH carries values.",
    "",
    "1. EXPECTED RETURN DEFAULTED TO ZERO.",
    "   _portfolio_stats did `m.get('expected_return_pct') or 0.0`, so a holding",
    "   with no metadata counted as returning exactly nothing. With 30% cash on",
    "   top, the app reported '~0%/yr vs your target 10%/yr - behind' for a book",
    "   that had actually gained 7.79%. Those are different quantities (expected",
    "   vs realized) but ~0%/yr was still wrong: an artefact of absent metadata.",
    "   Note the asymmetry it replaced - volatility fell back to a plausible 12%",
    "   while return fell back to 0.",
    "",
    "2. THE RISK SCORE HAD THE SAME HOLE.",
    "   compute_snapshot counted a holding with no volatility as a flat 15%, so",
    "   Risk was computed from a placeholder rather than from the book.",
    "",
    "   Both now fall back to strategy_profile.assumptions_for(), the existing",
    "   per-instrument-character table already used for strategy profiles and",
    "   already labelled planning assumptions rather than forecasts. A single",
    "   name models at ~9.5%/32%, a bond at ~3.5%/6%. No new numbers invented -",
    "   an existing grounded table is reused instead of a magic constant.",
    "",
    "3. THE CONCENTRATION CAP WAS NOT BEING APPLIED.",
    "   Live, every redeploy leg came back an identical 970.95. Weights were read",
    "   from snap['exposure_ticker'], which returned 0 for every ticker, so",
    "   size_purchase saw 'current weight 0' for AMZN at 29% of NAV and never",
    "   clipped it - the card proposed buying MORE of a holding already past the",
    "   25% cap. Weights are now derived from the rows themselves; a self-",
    "   contained calculation cannot drift from the caller's snapshot keys.",
    "",
    "4. THREE CARDS, ONE POT OF CASH.",
    "   Today showed 'Redeploy 3,884', 'Rebalance toward your target mix' and",
    "   'Add to SCHD - 7,899' at once: two different sizes for the same SCHD buy.",
    "   The redeploy card now also displaces the objective rebalance and any",
    "   sized buy_funded card for a ticker it already funds.",
    "",
    "+1 test; test_redeploy_cash now covers the empty-snapshot cap regression."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy, expect: expected return no longer ~0%/yr; Risk score" -ForegroundColor Cyan
Write-Host "moves (likely DOWN - single names model at ~32% vol, not the old 15%" -ForegroundColor Cyan
Write-Host "placeholder); no AMZN leg; one cash card instead of three." -ForegroundColor Cyan

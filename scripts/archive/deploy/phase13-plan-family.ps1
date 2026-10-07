# PHASE 13 - Beat the Market becomes a Plan family (Phase C).
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase13-plan-family.ps1
#
# USER-VISIBLE: a fifth tab appears on the Plan page. Its cards render from the
# existing renderer (risk chip, basket, buttons) but have no "Est. return" chips
# yet -- those numbers are measured, and the chip renderer reads `profile`.
# Phase F teaches the UI to read `backtest`. Until then the cards show name,
# description, risk, basket and working buttons.
#
# No migration. Requires phase 12 (strategy_backtests) to already be deployed.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/engines/strategy_backtest.py",
    "app/services/strategy_catalog.py",
    "app/services/strategy_service.py",
    "app/api/routes/strategy.py",
    "tests/test_strategy_backtest.py",
    "tests/test_backtest_service.py",
    "tests/test_strategy_profile.py",
    "scripts/smoke/smoke-phase10-13.ps1",
    "scripts/deploy/phase13-plan-family.ps1"
)

Invoke-Suite -Focus @("tests/test_backtest_service.py", "tests/test_strategy_backtest.py",
                      "tests/test_strategy_profile.py", "tests/test_plan_explore.py",
                      "tests/test_recos_apply.py")

$msg = @(
    "feat(strategy): Beat the Market becomes a Plan family (Phase C)",
    "",
    "The fifth family is rule-based, so its numbers are MEASURED by the",
    "nightly backtest rather than derived from the instrument-character",
    "lookup that describes a static basket. Cards are adapted to the shape",
    "the existing Plan renderer already reads instead of the renderer growing",
    "a second code path per field.",
    "",
    "* strategy_catalog.as_plan_cards() emits risk_tolerance in the",
    "  Low/Medium/High vocabulary, baskets as [ticker, weight] pairs, and",
    "  ``measured: true`` so the UI reads ``backtest`` instead of ``profile``.",
    "* A strategy with no stored result still renders, carrying",
    "  backtest: null -- the card can say `"not measured yet`" rather than",
    "  drawing a blank where a number belongs.",
    "* Each card carries a plain-language ``rule`` line stating the actual",
    "  mechanism. The description sells the idea; this says what the code",
    "  does, so a card cannot imply a discipline it does not implement.",
    "* as_legacy_strategy() lets preview / apply / load-basket resolve a",
    "  rule-based id through the SAME path as a static basket. Without it the",
    "  card's buttons were decorative.",
    "",
    "target_allocation is all-equity for every one of these. The rules move",
    "between an aggressive instrument and a core holding, both equity; time",
    "in T-bills is a transient state of the rule, not a target. Writing it in",
    "would make the allocation engine permanently demand a cash weight the",
    "strategy only wants sometimes.",
    "",
    "TWO ENGINE FIXES the production smoke run surfaced:",
    "",
    "1. A SHORT WINDOW NOW NAMES ITS CAUSE. btm_factor_stack spans 1721",
    "   sessions, not 2513, and the smoke could only shrug at it. A young",
    "   fund is fine; a truncated feed is a bug; they were indistinguishable.",
    "   Results now carry limiting_ticker, history_start_by_ticker and",
    "   history_capped_by_provider -- AVUV listing in 2019 is named as the",
    "   cause, and a short span with no young fund to explain it now FAILS.",
    "   Also adds sessions_per_year: real daily data lands near 252, and a",
    "   feed quietly serving monthly bars (Yahoo does this for range=max)",
    "   lands near 12 while a row count alone reads it as a long history.",
    "",
    "2. THE OVERFITTING VERDICT WAS MEASURED AGAINST ZERO. Splitting at 2022",
    "   puts a bear market entirely in the test half, so everything decays --",
    "   including buy-and-hold. Four of seven strategies were branded",
    "   `"likely overfitted`" for living through the same market as the index.",
    "   The verdict is now decay RELATIVE to the benchmark's decay across the",
    "   same split. btm_factor_stack decayed 11.33 against the benchmark's",
    "   12.41 -- it held up better than SPY and was being called overfitted.",
    "   Both decay figures are stored, so the judgement is auditable.",
    "",
    "Live production smoke (18 passed, 0 failed): all seven strategies",
    "computed, 252 sessions/yr, ledger invariant holding, nightly job",
    "registered.",
    "",
    "+7 tests. test_strategy_profile updated: it asserted every strategy",
    "carries a derived profile, which a measured one cannot honestly have --",
    "it now asserts the distinction rather than being weakened. ruff clean."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'After Railway reports Active:' -ForegroundColor Cyan
Write-Host '  1. POST /api/v1/strategies/backtests/refresh   (engine changed - recompute)' -ForegroundColor Gray
Write-Host '  2. .\scripts\smoke\smoke-phase10-13.ps1' -ForegroundColor Gray
Write-Host '  3. On the Pixel: Plan tab -> a fifth "Beat the Market" tab, 7 cards,' -ForegroundColor Gray
Write-Host '     each with a rule line and a working "What changes?" button.' -ForegroundColor Gray

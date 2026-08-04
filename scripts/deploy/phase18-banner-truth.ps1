# PHASE 18 - the triggered-rules banner counts cards, not flags.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase18-banner-truth.ps1
#
# Fixes smoke-all phase 6 ("banner 1 vs 0 cards").
#
# USER-VISIBLE + PWA: index.html and sw.js change, SW iw-v11 -> iw-v12. The red
# banner on Today may CLEAR on first load after this deploys -- that is the fix,
# not a regression: it was counting a rule with no card behind it.
#
# No migration.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/services/recommendations.py",
    "app/static_app/index.html",
    "app/static_app/sw.js",
    "tests/test_strategy_signals.py",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/deploy/phase18-banner-truth.ps1"
)

Invoke-Suite -Focus @("tests/test_strategy_signals.py", "tests/test_done_vs_ignored.py",
                      "tests/test_recommendations.py", "tests/test_accept_honesty.py",
                      "tests/test_reconcile.py", "tests/test_trading_rule_suggestions.py",
                      "tests/test_shell_cache_headers.py")

$msg = @(
    "fix(rules): the banner counts cards, not flags",
    "",
    "Reported by smoke-all phase 6: `"banner 1 vs 0 cards`" -- one triggered",
    "rule (V), no card on Today. A banner counting work with nothing left to",
    "tap cannot be cleared by tapping anything, so it stays red forever.",
    "",
    "ROOT SHAPE, NOT ROOT CAUSE. The banner read ``triggered`` off /rules while",
    "the cards came from the recommendations pipeline: two sources for one",
    "fact. The existing self-heal covers exactly one drift path (a card that",
    "existed and was hidden by an earlier dismissal). Every other path -- and",
    "I could not reproduce which one produced V from outside production --",
    "ends identically: a latched flag no card can clear. So this fixes the",
    "shape rather than guessing the cause.",
    "",
    "* build_recommendations now reconciles at the end: any rule that is",
    "  active + triggered but produced no VISIBLE card is a contradiction, so",
    "  it is resolved as acknowledged and logged. Guarded on the rules agent",
    "  having succeeded -- if it degraded, the absence of cards says nothing",
    "  about the rules, and retiring them would destroy real pending work",
    "  over a transient provider failure.",
    "* The response carries rule_banner {triggered, carded, healed}, so the",
    "  reconciliation is auditable rather than silent.",
    "* The UI banner counts only rules the server confirms produced a visible",
    "  card. Both payloads arrive asynchronously, so each re-renders on",
    "  arrival and the pre-recommendations paint falls back to the old flag",
    "  count -- never worse than today's behaviour.",
    "",
    "Smoke now asserts banner == rule cards, and reports anything it healed.",
    "",
    "Also adds -ApplyStrategy to the Beat the Market smoke. The signal and",
    "discipline checks were skipping because no rule-based strategy was",
    "applied, and a permanent SKIP is indistinguishable from a check that",
    "does not work. It now FAILS with the exact command that fixes it, and",
    "the switch applies one (the only write in an otherwise read-only",
    "script).",
    "",
    "SW iw-v11 -> iw-v12. +1 test."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host ''
Write-Host 'Then, in order:' -ForegroundColor Cyan
Write-Host '  .\scripts\smoke\smoke-all.ps1                 # phase 6 should now pass' -ForegroundColor Gray
Write-Host '  .\scripts\smoke\smoke-beat-market.ps1 -ApplyStrategy btm_trend_tqqq' -ForegroundColor Gray
Write-Host '     (that switch WRITES: it sets your plan objective, risk and strategy)' -ForegroundColor DarkGray
Write-Host '  .\scripts\smoke\smoke-beat-market.ps1 -Refresh' -ForegroundColor Gray
Write-Host ''
Write-Host 'On the Pixel: hard-refresh for SW iw-v12, then check the Today banner' -ForegroundColor Gray
Write-Host 'either clears or matches the number of rule cards below it.' -ForegroundColor Gray

# PHASE 8 - the rule banner can no longer deadlock.
#
# Found by the phase 6+7 smoke test: "banner says 1 but Today shows 0".
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase8-banner-heal.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @("app/services/recommendations.py", "STATUS.md")

Invoke-Suite -Focus @("tests/test_rule_actions.py", "tests/test_rule_resolution.py",
                      "tests/test_done_vs_ignored.py", "tests/test_recommendations.py")

$msg = @(
    "fix(rules): self-heal a triggered rule whose card is already suppressed",
    "",
    "Caught by the phase 6+7 smoke test: the banner reported 1 triggered rule",
    "while Today rendered 0 rule cards.",
    "",
    "A rule latches `triggered`, but its CARD can be independently hidden by a",
    "7-day dismissal or a 90-day completion. Act on a card and the rule clears",
    "(phase 6) - but any rule acted on BEFORE phase 6 shipped kept its latch",
    "while its card stayed suppressed. Result: the red banner counts work with",
    "nothing left to click, and no user action can resolve it because the card",
    "that would do so is hidden. It would persist for the life of the rule.",
    "",
    "Being suppressed means the user already dealt with it, so build_recommendations",
    "now resolves any triggered rule whose card is in the dismissed or completed",
    "set, immediately before filtering. Self-heals the legacy state on the next",
    "Today load and closes the deadlock permanently. Cleanup failures are logged,",
    "never raised - a housekeeping problem must not break the Today view.",
    "",
    "The smoke test's banner-vs-cards cross-check is what surfaced this; two",
    "sources of truth for 'what is outstanding' will always drift eventually."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nAfter deploy, re-run the smoke test - check 3 should pass:" -ForegroundColor Cyan
Write-Host "  .\scripts\smoke\smoke-phase67.ps1"

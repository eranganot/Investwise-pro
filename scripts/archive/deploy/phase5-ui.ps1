# PHASE 5 - Frontend: Rule history panel + honest health-score copy.
#
# index.html and sw.js are shared by phases 2 and 3, so they ship together here
# rather than being split across commits. Run this LAST - it carries the service
# worker bump (iw-v10 -> iw-v11) that pushes the new shell to your phone.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase5-ui.ps1

. "$PSScriptRoot\_common.ps1"
Enter-Repo

$files = @(
    "app/static_app/index.html",
    "app/static_app/sw.js"
)

# Guard against the truncation footgun documented in STATUS.md: a UI-only
# change that shows a four-figure deletion is a truncated write, not a diff.
$shellPath = Join-Path $RepoRoot "app/static_app/index.html"
$lines = (Get-Content $shellPath).Count
if ($lines -lt 1400) {
    Write-Host "index.html is only $lines lines - looks truncated. Aborting." -ForegroundColor Red
    Write-Host "Restore with: git checkout -- app/static_app/index.html" -ForegroundColor Red
    exit 1
}
if (-not (Select-String -Path $shellPath -Pattern "</html>" -Quiet)) {
    Write-Host "index.html has no closing </html> - truncated write. Aborting." -ForegroundColor Red
    exit 1
}
Write-Host "index.html: $lines lines, closing tag present." -ForegroundColor Green

Invoke-Suite

$msg = @(
    "fix(push): re-subscribe when permission is granted but the sub is gone",
    "",
    "ROOT CAUSE of the two-week notification outage, found from live",
    "/api/v1/push/status: the dedupe ledger's newest entry was 2026-07-18T18:47",
    "- the exact day the alignment batch shipped SW iw-v9 -> iw-v10. Replacing",
    "the service worker dropped the browser push subscription, the server pruned",
    "the dead endpoint (subscriptions: 0), and initNotifyState only re-registered",
    "a subscription that already existed: with permission still granted and",
    "sub === null it fell through to _setNotifyState('off') and gave up. Nothing",
    "ever re-created it, so the app looked merely switched off rather than broken",
    "and the silence was permanent and invisible.",
    "",
    "Now: if permission is granted and no subscription exists, mint a new one and",
    "register it. This closes the recurrence path for every future SW bump.",
    "",
    "Note: the phase 4 hardening (403 pruning, misfire grace, job watchdog) was",
    "real but was NOT the cause. Its value was /push/status, which surfaced the",
    "July 18 timestamp that made this diagnosable at all.",
    "",
    "Also in this commit:",
    "",
    "Rule history (Holdings -> Rules): a collapsible log of every firing - when,",
    "at what price against which target, whether a push actually went out, and",
    "what was executed (shares, cash credited, estimated tax). Answers 'I see the",
    "rule triggered but I can't see the actions that were taken'. Executed rows",
    "state explicitly that only the tracked book changed and no brokerage order",
    "was placed.",
    "",
    "Health explainer rewritten: it advertised '25% Risk + 25% Tax + 20% Spread +",
    "15% Cash + 15% themes' while showing only four chips - the 15% themes",
    "component was both invisible and pinned to a constant. Now states the four",
    "measured weights, that all four are displayed, and that 100 is reachable.",
    "The risk explainer no longer describes the old 100 - vol x 2 formula.",
    "",
    "SW cache iw-v10 -> iw-v11 so the new shell actually reaches the client."
)

if (New-Commit -Files $files -Message $msg) { Push-AndWatch }

Write-Host "`nPixel 9 QA after this deploy:" -ForegroundColor Cyan
Write-Host "  - Hard-refresh / reopen the PWA; confirm the shell is iw-v11"
Write-Host "  - Holdings -> Rules -> 'Rule history' lists the MSFT/META firings"
Write-Host "  - A triggered stop-loss card now says 'The app can do this for you'"
Write-Host "    and Accept removes the position + credits cash"
Write-Host "  - Tap 'what's this?' on the health score - four components, no themes"

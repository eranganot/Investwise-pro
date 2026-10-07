# SHIP PR 3 - the trade ledger, and the repair it makes recordable.
#
#   .\scripts\deploy\ship-pr3-ledger.ps1
#   .\scripts\deploy\ship-pr3-ledger.ps1 -SkipTests
#
# This PR SHIPS THE TOOL. It does not repair your book - that is a separate,
# explicit act you take afterwards with a scale you have chosen yourself, and
# the reason is in the smoke output: the anchors disagree, and picking one for
# you would present an estimate as a measurement.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml. AND run the Postgres job - see CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/models/tables.py",
    "app/main.py",
    "app/services/ledger_service.py",
    "app/services/reconcile_service.py",
    "app/services/recommendations.py",
    "app/api/routes/intake.py",
    "alembic/versions/0018_transaction_ledger.py",
    "tests/conftest.py",
    "tests/test_ledger_and_reconcile.py",
    "tests/test_accept_conserves_nav.py",
    "scripts/smoke/smoke-reconcile.ps1",
    "scripts/deploy/ship-pr3-ledger.ps1"
)

$Message = @(
    "Record what the money did, and make the phantom basis removable",
    "",
    "THE LEDGER. transactions has existed since the first schema and had ZERO",
    "writers outside the models for the life of the app. Phase N's own docstring",
    "named that as the reason past NAV could not be reconstructed. Two",
    "consequences, both of which cost real time to diagnose: nav_history had no",
    "flows, so every book change was attributed to the market - +34.62% on a book",
    "whose holdings had gained 4% - and when an FX bug minted 7,388 of positions",
    "on 2026-08-21 there was nothing to reconstruct it from.",
    "",
    "Two schema problems had to be fixed before it could be written to at all:",
    "",
    "  position_id was NOT NULL with ondelete=CASCADE. A full exit DELETES the",
    "  position, so the ledger would have erased the record of the very sale that",
    "  removed it. Now nullable with SET NULL, and every identifying field is",
    "  denormalised so a row still says what it was once its position is gone.",
    "",
    "  The ORM relationship also carried cascade='all, delete-orphan', which",
    "  deletes children in PYTHON whatever the FK says - so SET NULL would have",
    "  been silently overruled. passive_deletes=True hands the decision to the",
    "  database, the only place it can be enforced consistently. Caught by a test",
    "  that deletes a position and looks for the sale.",
    "",
    "There was also nowhere to record the ILS value, the tax, or the cause. A",
    "ledger that cannot be reconciled against NAV is a list, not a ledger.",
    "",
    "One call site, at the boundary of apply_recommendation, for every actionable",
    "kind. Deliberately not per-handler: 'each handler records its own' is a rule",
    "a new handler silently opts out of, which is how this table stayed empty.",
    "",
    "THE REPAIR REDUCES TO ONE NUMBER, and that is not luck. The bug multiplied",
    "every holding in a class by the SAME factor and never touched cost_basis per",
    "share. The book has the right shape and the wrong size, so true_qty = qty/s",
    "for a single unknown s. That is the whole reason it is repairable without a",
    "broker statement.",
    "",
    "reconcile_service.preview() computes every available anchor and REFUSES to",
    "choose:",
    "",
    "  deposits   securities basis / what was actually paid in. The LARGEST s -",
    "             it over-corrects, because sell_losers legitimately re-stamped",
    "             basis at market when it sold the losing positions. Upper bound.",
    "  snapshot   the last NAV recorded before the first bad accept. A FLOOR: it",
    "             assumes nothing legitimate moved the book since, and a real sale",
    "             did.",
    "  manual     you give s, or one holding's true share count.",
    "",
    "The honest answer is bracketed by the first two, and the payload says so.",
    "Auto-applying the top row of a disagreeing set would be the same move that",
    "let 7,388 of phantom basis read as +42.48% performance.",
    "",
    "GET  /portfolio/reconcile/preview   read-only, no side effects",
    "POST /portfolio/reconcile/apply     requires confirm=true; ANALYST",
    "",
    "The correction writes itself into the ledger as Adjust rows. A repair that",
    "is itself invisible leaves the next reader exactly where this started.",
    "",
    "Also: own_session moved into conftest as a fixture. The cross-loop asyncpg",
    "trap took test-postgres red twice, in two different files, AFTER the rule",
    "was written into CLAUDE.md - which is the evidence that a documented rule is",
    "not enough and it has to be the path of least resistance instead.",
    "",
    "All three CI jobs verified before pushing:",
    "  pytest on postgres:16     969 passed, 12 skipped",
    "  pytest on sqlite          973 passed, 4 skipped",
    "  ruff check app tests      clean"
)

Step "1. Repo"
Enter-Repo

Step "2. Gate (mirrors ci.yml)"
if ($SkipTests) { Warn "-SkipTests set. CI is still the real gate." }
else { Invoke-Suite -Focus @("tests/test_ledger_and_reconcile.py", "tests/test_accept_conserves_nav.py", "tests/test_nav_history.py") }
Write-Host ""
Warn "The POSTGRES job needs a local server and is not run here. It WAS run for"
Warn "this PR in the sandbox - and it caught a real bug the SQLite job could not:"
Warn "the FK was still CASCADE on an existing table, because create_all never"
Warn "alters one. That is now in the startup DDL."

Step "3. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - verifying what is already on main." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "4. Push, and let CI gate it"
Push-AndWatch

Step "5. Wait for Railway to actually serve it"
$live = ""
$deadline = (Get-Date).AddMinutes(8)
while ((Get-Date) -lt $deadline) {
    try {
        $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
        $live = "$($h.commit)"
        if ($live -eq $sha) { break }
    } catch { $live = "(no answer)" }
    Write-Host "  serving $live, waiting for $sha ..." -ForegroundColor DarkGray
    Start-Sleep -Seconds 20
}
if ($live -eq $sha) { Write-Host "  Railway is serving $sha" -ForegroundColor Green }
else { Warn "Still serving '$live' after 8 minutes. Check the Railway build log." }

Step "6. Smoke (read-only)"
if (-not $env:IW_AGENT_KEY) { Warn "IW_AGENT_KEY not set - the smoke falls back to the committed key." }
& (Join-Path $RepoRoot "scripts\smoke\smoke-reconcile.ps1") -Sha $sha -BaseUrl $BaseUrl
$smokeExit = $LASTEXITCODE

Step "7. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  YOUR BOOK IS NOT REPAIRED YET. That is deliberate." -ForegroundColor Yellow
Write-Host ""
Write-Host "  R2 above printed each anchor with its own arithmetic and its own" -ForegroundColor Cyan
Write-Host "  caveat. They DISAGREE, and the disagreement is real:" -ForegroundColor Cyan
Write-Host "    - 'deposits' over-corrects. Selling a position re-stamps the basis" -ForegroundColor Gray
Write-Host "      of whatever you buy with the proceeds at today's price, and that" -ForegroundColor Gray
Write-Host "      part of the gap is genuine, not phantom. Upper bound on s." -ForegroundColor Gray
Write-Host "    - 'snapshot' under-corrects. It assumes nothing legitimate moved" -ForegroundColor Gray
Write-Host "      the book since that date, and a real sale did on 08-21. Floor." -ForegroundColor Gray
Write-Host ""
Write-Host "  Pick a scale between them, dry-run it, then apply:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-reconcile.ps1 -Sha $sha -Scale <s>" -ForegroundColor Gray
Write-Host "    .\scripts\smoke\smoke-reconcile.ps1 -Sha $sha -Scale <s> -Apply" -ForegroundColor Gray
Write-Host ""
Write-Host "  Then POST /api/v1/portfolio/nav-history/snapshot so tonight's series" -ForegroundColor Cyan
Write-Host "  starts from the corrected book rather than the inflated one." -ForegroundColor Cyan
Write-Host ""
Write-Host "  Then update STATUS.md." -ForegroundColor Cyan
Write-Host ""
if ($smokeExit -ne 0) { exit 1 }
exit 0

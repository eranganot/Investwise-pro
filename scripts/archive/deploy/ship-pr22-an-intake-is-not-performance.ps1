# SHIP PR 22 - a holding you entered is not a month you had.
#
#   .\scripts\deploy\ship-pr22-an-intake-is-not-performance.ps1
#   .\scripts\deploy\ship-pr22-an-intake-is-not-performance.ps1 -SkipTests
#
# No migration. No engine version bump. YOUR ACCOUNT-HISTORY PERCENTAGE WILL
# CHANGE if a holding was ever entered inside the window you are looking at -
# read section 0.
#
# REQUIREMENTS CHANGED: py-vapid is now pinned. Re-run
#   pip install -r requirements.txt -r requirements-dev.txt
# before the gate, or you are testing the resolution this PR exists to remove.
#
# House rules: never "git add -A"; commit via "git commit -F"; the local gate
# mirrors ci.yml EXACTLY - plain "pytest -q", no extra flags. AND run the
# Postgres job. See CLAUDE.md.

param(
    [switch]$SkipTests,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

. "$PSScriptRoot\_common.ps1"

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }

$Files = @(
    "app/services/nav_history.py",
    "app/services/push_service.py",
    "app/static_app/index.html",
    "requirements.txt",
    "tests/test_history_does_not_call_an_intake_performance.py",
    "tests/test_vapid_keys_are_generated_cleanly.py",
    "scripts/deploy/ship-pr22-an-intake-is-not-performance.ps1",
    "STATUS.md"
)

$Message = @(
    "PR 22: the history chart called an intake +35%, and the caption vouched for it",
    "",
    "Reported live 2026-08-22 and open since: the account-history chart showed",
    "+35% on the day a holding was added through the Holdings screen, under a",
    "caption reading 'No deposits or withdrawals in this window, so this is",
    "simply what your money did.'",
    "",
    "Both halves false. THE SECOND IS THE WORSE ONE - a caption that vouches for",
    "a number tells the reader the checking has already been done.",
    "",
    "ROOT CAUSE. time_weighted takes its flows from the CONTRIBUTIONS LEDGER.",
    "Adding a holding on the Holdings screen writes no ledger row, so",
    "net_flow_between returned 0.00 and the entire jump in NAV compounded as",
    "return.",
    "",
    "THE SIGNAL WAS ALREADY ON THE ROW AND NOTHING READ IT. NavSnapshot.positions",
    "is stored on every snapshot, and its own class docstring says why: 'a NAV",
    "that jumped because money arrived is not a return, and without the deposit",
    "total on the same row a later reader cannot tell the two apart.' series_for",
    "did not even pass it to time_weighted.",
    "",
    "MEASURED on the shape of the incident - flat at 10,000, a fourth holding",
    "entered at 13,500, flat after:",
    "",
    "  before   total_pct = +35.0%   nothing flagged",
    "  after    total_pct =   0.0%   the 2026-08-22 step named: +3,500 ILS, 3->4",
    "",
    "THE FIX, AND THE JUDGEMENT INSIDE IT. When the position count changes and",
    "the ledger says nothing, the unexplained delta is treated as an external",
    "flow - the same thing the app already does for a deposit. That IS an",
    "attribution: the snapshot records a count and a total, never which holding",
    "arrived or what it was worth. So it is reported in unexplained_flows rather",
    "than folded in silently, and the caption names the date, the direction and",
    "the amount, and says what to do about it. Guessing quietly is how the +35%",
    "happened; guessing quietly in the other direction is not the fix.",
    "",
    "The ledger stays authoritative where it has something to say, so a deposit",
    "made on the same day a holding was entered is not counted twice.",
    "",
    "ALSO IN HERE: WEB PUSH WAS ONE DEPENDENCY BUMP FROM BREAKING.",
    "",
    "Eran's gate showed '1371 passed, 2 warnings', both",
    "CryptographyDeprecationWarning: 'Curve argument must be an instance of an",
    "EllipticCurve class ... This will be an exception in a future version', from",
    "py_vapid's generate_keys().",
    "",
    "Not cosmetic, and further along than the message says. MEASURED on",
    "cryptography 50.0.1, that exact call already RAISES:",
    "",
    "    TypeError: curve must be an EllipticCurve instance",
    "",
    "The repo pins cryptography==44.0.0, where it still only warns. So the fuse",
    "has already burned down in the current release and the pin is the only",
    "insulation. On the bump, VAPID key generation raises and web push stops, for",
    "a reason nobody would connect to notifications.",
    "",
    "AND IT DID NOT REPRODUCE IN THE SANDBOX, which is the second half of the",
    "defect. Whether it fires depends on WHICH py_vapid is installed - 1.9.4",
    "passes an instance, older releases pass the class - and pywebpush==2.3.0",
    "requires only py-vapid>=1.7.0: an open lower bound in a file where every",
    "other line is ==. Two machines legitimately resolved two different",
    "libraries and only one warned.",
    "",
    "Both halves fixed. requirements.txt pins py-vapid==1.9.4, and",
    "_generate_vapid builds the keypair with cryptography directly - which the",
    "two lines after it were already doing. A VAPID key is an ordinary EC P-256",
    "keypair; py_vapid was being asked for one call it does not need to own. Four",
    "tests, including the round trip proving py_vapid still derives the same",
    "public key from our private one - a mismatch there is the silent 403 that",
    "kills every existing subscription.",
    "",
    "Gate: SQLite 1365 passed / 0 skipped / 0 warnings; Postgres 1365 passed / 0 skipped / 0 warnings; ruff check app tests",
    "clean, ZERO SKIPS AND ZERO WARNINGS. All ten new tests fail on the old code.",
    "",
    "Not financial advice."
)

Step "0. What this changes about what you see"
Write-Host "  THE ACCOUNT-HISTORY PERCENTAGE WILL CHANGE if a holding was ever" -ForegroundColor Yellow
Write-Host "  entered inside the window you are looking at. It will read LOWER," -ForegroundColor Yellow
Write-Host "  and the old number was counting your own holding as a gain." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Where that happens the caption now names the date and the amount" -ForegroundColor Yellow
Write-Host "  instead of claiming there were no flows. If one of those really" -ForegroundColor Yellow
Write-Host "  was a deposit, record it under Contributions and the line goes." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Push notifications: no visible change. The keypair is generated" -ForegroundColor Yellow
Write-Host "  the same way and existing subscriptions keep working - there is a" -ForegroundColor Yellow
Write-Host "  round-trip test for exactly that." -ForegroundColor Yellow
Write-Host ""
if ((Read-Host "Continue? (y/N)") -ne "y") { exit 1 }

Step "1. Repo + sync"
Enter-Repo

Step "2. Dependencies - py-vapid is newly pinned"
Write-Host "  This PR pins py-vapid==1.9.4. If your environment still has the" -ForegroundColor Yellow
Write-Host "  older one, the gate below tests the resolution the PR removes." -ForegroundColor Yellow
Write-Host "    pip install -r requirements.txt -r requirements-dev.txt" -ForegroundColor White
if ((Read-Host "  Installed? (y/N)") -ne "y") {
    Write-Host "  Run it, then re-run this script." -ForegroundColor Red
    exit 1
}

Step "3. The gate - all three CI jobs"
if ($SkipTests) {
    Warn "-SkipTests: the gate did NOT run."
} else {
    Invoke-Suite -Focus @(
        "tests/test_history_does_not_call_an_intake_performance.py",
        "tests/test_vapid_keys_are_generated_cleanly.py",
        "tests/test_nav_history.py",
        "tests/test_the_projection_sees_your_deposits.py")
    Write-Host ""
    Write-Host "  READ THE SUMMARY LINE. It must say neither 'skipped' nor" -ForegroundColor Yellow
    Write-Host "  'warnings'. The two py_vapid warnings are what this PR removes;" -ForegroundColor Yellow
    Write-Host "  if they are still there, the old py-vapid is still installed." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Then run test-postgres. Recipe: CLAUDE.md." -ForegroundColor Yellow
    if ((Read-Host "  Did test-postgres pass, 0 skipped and 0 warnings? (y/N)") -ne "y") {
        Write-Host "  Stopping. Run it, then re-run this script." -ForegroundColor Red
        exit 1
    }
}

Step "4. Commit"
$committed = New-Commit -Files $Files -Message $Message
if (-not $committed) { Warn "Nothing new to commit - the patch was applied with git am. Pushing it." }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  local HEAD: $sha" -ForegroundColor Green

Step "5. Push, and let CI gate it"
Push-AndWatch

Step "6. Wait for Railway to actually serve it"
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

Step "7. Smoke"
Write-Host "    .\scripts\smoke\smoke-history.ps1 -Sha $sha" -ForegroundColor White
Write-Host "  The caption is the half only your own eyes can check - see below." -ForegroundColor DarkGray

Step "8. Done"
Write-Host ""
Write-Host "  main:    $sha" -ForegroundColor Green
Write-Host "  serving: $live" -ForegroundColor Green
Write-Host ""
Write-Host "  CHECK WITH YOUR OWN EYES:" -ForegroundColor Cyan
Write-Host "  * Today, the account-history chart: over a window in which you" -ForegroundColor Cyan
Write-Host "    added a holding, the percentage should be LOWER than before and" -ForegroundColor Cyan
Write-Host "    the caption should NAME that day, the direction and the amount." -ForegroundColor Cyan
Write-Host "  * It must no longer say 'No deposits or withdrawals in this" -ForegroundColor Cyan
Write-Host "    window' on a window where your holdings changed. That sentence" -ForegroundColor Cyan
Write-Host "    was the worse half of this bug." -ForegroundColor Cyan
Write-Host "  * /api/v1/portfolio/nav-history now carries unexplained_flows[]." -ForegroundColor Cyan
Write-Host "  * Notifications should be unaffected. If a push stops arriving," -ForegroundColor Cyan
Write-Host "    tell me immediately - that would be the VAPID round trip, and" -ForegroundColor Cyan
Write-Host "    it is the one thing in here that could break silently." -ForegroundColor Cyan
Write-Host ""
Write-Host "  STILL BROKEN ON PURPOSE:" -ForegroundColor DarkYellow
Write-Host "  * The +739.2 ILS leak is UNEXPLAINED. Twelve full local runs did" -ForegroundColor DarkYellow
Write-Host "    not reproduce it. When CI next catches it the failure prints" -ForegroundColor DarkYellow
Write-Host "    the API NAV, the in-session drift, the apply spec, every" -ForegroundColor DarkYellow
Write-Host "    position that moved and the cards emitted. SEND ME THAT BLOCK." -ForegroundColor DarkYellow
Write-Host "  * VAPID keys are still generated and stored in the DB rather than" -ForegroundColor DarkYellow
Write-Host "    pinned in Railway. If that table is ever reset, every existing" -ForegroundColor DarkYellow
Write-Host "    subscription dies silently. That is the open backlog item." -ForegroundColor DarkYellow
Write-Host "  * fx_rate REPORTS an unresolvable currency; it does not ABSTAIN." -ForegroundColor DarkYellow
Write-Host "  * Find-the-best-split still searches up to 97% rather than the" -ForegroundColor DarkYellow
Write-Host "    75% you accepted. PR 23 - after that the bucket is empty." -ForegroundColor DarkYellow
Write-Host "  * There is still not one Tel Aviv LISTING in the candidate" -ForegroundColor DarkYellow
Write-Host "    universe, so no card can name one. PR 24." -ForegroundColor DarkYellow
Write-Host "  * The AGENT_API_KEY is still a literal in 34 committed scripts." -ForegroundColor DarkYellow
Write-Host "  * Not financial advice." -ForegroundColor DarkYellow
Write-Host ""
exit 0

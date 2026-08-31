# SHIP - PR 26. Two published credentials, and the reason one of them could
# not be changed.
#
#   .\scripts\deploy\ship-pr26-the-published-credentials.ps1
#   .\scripts\deploy\ship-pr26-the-published-credentials.ps1 -SkipTests
#
# WHAT WAS WRONG, measured against production on 2026-08-30:
#
#   GET  /api/v1/portfolio  (no key)                    -> 401
#   GET  /api/v1/portfolio  (a wrong key)               -> 401
#   GET  /api/v1/portfolio  (the key in 33 scripts)     -> 200
#   POST /api/v1/auth/token (the default from config.py)-> 200, SUPERADMIN
#
# Authentication worked perfectly. Both credentials were simply public: this
# repository was public until 2026-08-30.
#
# The password half was the worse one, because the documented remedy did
# nothing. PRODUCTION.md section 8 says to set AUTH_PASSWORD.
# ensure_superadmin_credential only ever ran an INSERT -- "if ... is None" --
# so the hash was written once at first boot and every later change to the
# variable was ignored. There is no password-change endpoint in this app. The
# published password could not be retired by any configuration at all.
#
# THIS SCRIPT DOES NOT ROTATE ANYTHING BY ITSELF. It generates the two new
# values, saves the agent key where the scripts look for it, and then stops and
# waits: the rotation happens when YOU set them on Railway. See ROTATION.md.
#
# House rules (_common.ps1): never git add -A; commit message via -F, no BOM;
# no backticks; nothing here needs PowerShell 7.

param(
    [switch]$SkipTests,
    [switch]$NoGenerate)

. "$PSScriptRoot\_common.ps1"

Write-Host ""
Write-Host "PR 26 - the published credentials" -ForegroundColor White
Write-Host ""

Enter-Repo

# ---------------------------------------------------------------------------
# 1. Generate the replacements, locally, now.
#
# Generated HERE and never written into a tracked file. That is the whole
# lesson of this PR: a credential that a script can read out of the repository
# is not a credential. scripts\.agent-key is git-ignored (.gitignore line 31)
# and tests\test_no_secret_is_committed.py fails the build if that stops being
# true.
# ---------------------------------------------------------------------------
$newKey = $null
$newPassword = $null

if (-not $NoGenerate) {
    Write-Host "1. Generating replacements" -ForegroundColor Cyan

    $newKey = (python -c "import secrets; print('iwk_' + secrets.token_urlsafe(36))").Trim()
    $newPassword = (python -c "import secrets; print(secrets.token_urlsafe(24))").Trim()

    if (-not $newKey -or -not $newPassword) {
        Write-Host "   Could not generate values - is python on PATH?" -ForegroundColor Red
        exit 1
    }

    $keyFile = Join-Path $RepoRoot "scripts\.agent-key"
    if (Test-Path $keyFile) {
        $backup = "$keyFile.previous"
        Copy-Item $keyFile $backup -Force
        Write-Host "   existing scripts\.agent-key copied to .agent-key.previous" -ForegroundColor DarkGray
    }
    # No BOM, one line, nothing else. Get-AgentKey reads the first line only.
    [System.IO.File]::WriteAllLines(
        $keyFile, [string[]]@($newKey), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "   wrote scripts\.agent-key (git-ignored)" -ForegroundColor DarkGray

    # Prove the ignore rule actually covers it, on THIS machine, right now.
    # A one-line assertion beats trusting that .gitignore says what it means.
    git check-ignore -q -- "scripts/.agent-key"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   scripts\.agent-key is NOT ignored by git. Refusing to continue." -ForegroundColor Red
        Write-Host "   Delete it and fix .gitignore before shipping." -ForegroundColor Red
        exit 1
    }
    Write-Host "   confirmed git ignores it" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 2. Tests
# ---------------------------------------------------------------------------
if (-not $SkipTests) {
    Invoke-Suite -Focus @("tests/test_auth_service.py",
                          "tests/test_no_secret_is_committed.py")
} else {
    Write-Host ""
    Write-Host "2. Tests SKIPPED at your request." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 3. Commit
# ---------------------------------------------------------------------------
$files = @(
    "app/core/config.py",
    "app/services/auth_service.py",
    "tests/test_auth_service.py",
    "tests/test_no_secret_is_committed.py",
    "scripts/agent-key.ps1",
    "scripts/get-error-log.ps1",
    "scripts/deploy/ship-pr5b-dead-tickers.ps1",
    "scripts/deploy/ship-pr8-drop-legacy-columns.ps1",
    "scripts/deploy/ship-pr9-card-scope.ps1",
    "scripts/deploy/ship-pr10-bonds.ps1",
    "scripts/deploy/ship-pr11-t5-solver-constraint.ps1",
    "scripts/deploy/ship-pr12-core-card-and-price-refresh.ps1",
    "scripts/deploy/ship-pr13-t6b-core-search.ps1",
    "scripts/deploy/ship-pr26-the-published-credentials.ps1",
    "scripts/smoke/smoke-all.ps1",
    "scripts/smoke/smoke-beat-market.ps1",
    "scripts/smoke/smoke-bonds.ps1",
    "scripts/smoke/smoke-card-scope.ps1",
    "scripts/smoke/smoke-cards.ps1",
    "scripts/smoke/smoke-core-card-and-price-refresh.ps1",
    "scripts/smoke/smoke-core-picker.ps1",
    "scripts/smoke/smoke-e2e.ps1",
    "scripts/smoke/smoke-history.ps1",
    "scripts/smoke/smoke-measured-assumptions.ps1",
    "scripts/smoke/smoke-money-safety.ps1",
    "scripts/smoke/smoke-p0.ps1",
    "scripts/smoke/smoke-p1.ps1",
    "scripts/smoke/smoke-p2.ps1",
    "scripts/smoke/smoke-p3.ps1",
    "scripts/smoke/smoke-p4.ps1",
    "scripts/smoke/smoke-phase67.ps1",
    "scripts/smoke/smoke-phase9.ps1",
    "scripts/smoke/smoke-reconcile.ps1",
    "scripts/smoke/smoke-t5-solver-constraint.ps1",
    "scripts/smoke/smoke-t6b-core-search.ps1",
    "scripts/smoke/smoke-t6b-sleeve-count.ps1",
    "scripts/smoke/smoke-truth.ps1",
    "scripts/smoke/smoke-w1-walk-forward.ps1",
    "scripts/smoke/smoke-w3-contributions.ps1",
    "scripts/smoke/smoke-the-published-credentials-are-dead.ps1",
    "ROTATION.md",
    "PRODUCTION.md",
    "STATUS.md"
)

$msg = @(
    "PR 26: two credentials were published, and one of them could not be changed",
    "",
    "SYMPTOM     The repository was public. Anyone who read it could log into",
    "            production as the owner, and could write to it as SUPERADMIN.",
    "",
    "EVIDENCE    Measured against production 2026-08-30, before this commit:",
    "              GET  /api/v1/portfolio   no key             -> 401",
    "              GET  /api/v1/portfolio   a wrong key        -> 401",
    "              GET  /api/v1/portfolio   the committed key  -> 200",
    "              POST /api/v1/auth/token  the config default -> 200 SUPERADMIN",
    "            33 tracked scripts carried the key as a literal; one distinct",
    "            value, in 160+ commits of history.",
    "",
    "RULED OUT   Auth not enforced - a wrong key and no key both got 401.",
    "            A leak of anything else - swept every tracked file and all of",
    "            history for key shapes, .env/.pem files and connection strings",
    "            with passwords. One iwk_ value, nothing else. The FMP key has",
    "            never been in the repo.",
    "",
    "ROOT CAUSE  Two, and the second is why the first lasted five months.",
    "            (1) config.py gave auth_password a literal default value in a",
    "                public repo. A published default is not a weak password;",
    "                it is a password everyone has. It is named in",
    "                auth_service.PUBLISHED_PASSWORDS and nowhere else now.",
    "            (2) auth_service.ensure_superadmin_credential only ever ran an",
    "                INSERT - 'if ... is None'. The hash was written once at",
    "                first boot and AUTH_PASSWORD was never read again. There is",
    "                no password-change endpoint. So the remedy PRODUCTION.md",
    "                section 8 tells you to perform did nothing, and no",
    "                configuration could retire the published password.",
    "",
    "WHY SILENT  Nothing failed. Auth worked. The test for this code path",
    "            asserted the hole: test_ensure_credential_and_login logged in",
    "            with that same default and passed every run, because the test",
    "            hard-coded what the config did. A test written",
    "            against a default cannot notice the default is published.",
    "",
    "BLAST RADIUS  One Credential creation site in the codebase; checked. Google",
    "            sign-in is a separate path and is untouched. The old values stay",
    "            in git history forever - rotation makes them useless, it does",
    "            not remove them. Making the repo private on 2026-08-30 closed",
    "            new exposure; it did not un-publish what was already out.",
    "",
    "CONFIDENCE  PROVEN. Both holes were exercised against the live service, and",
    "            all four new auth tests fail against the old code and pass",
    "            against the new.",
    "",
    "FIX",
    "  * auth_password has no default. Blank means password login is",
    "    UNAVAILABLE rather than open. Fail-closed on purpose.",
    "  * AUTH_PASSWORD is now authoritative: a stored hash that does not verify",
    "    against it is replaced. This is the app's only means of changing the",
    "    SuperAdmin password and it did not exist before.",
    "  * PUBLISHED_PASSWORDS is refused even if set deliberately, and an",
    "    existing row still holding one is scrambled to a random value.",
    "    /api/v1/auth/token awaits that BEFORE checking credentials, so the",
    "    first request after deploy retires the old hash - including a request",
    "    from someone arriving to use it.",
    "  * The key literal is gone from all 33 scripts; they call Get-AgentKey.",
    "    agent-key.ps1 lost its third resolution candidate (read the literal",
    "    back out of smoke-p0.ps1), which would have quietly undone this, and",
    "    gained a SHA-256 fingerprint check that warns when the retired key is",
    "    still in use - a hash, not the value.",
    "  * ROTATION.md is the runbook. PRODUCTION.md section 8 now says that its",
    "    own instruction used to be inert.",
    "",
    "CHECK THAT WOULD HAVE CAUGHT IT",
    "  tests/test_no_secret_is_committed.py reads the files git would carry -",
    "  tracked AND new un-ignored ones - and fails on key shapes, on the two",
    "  burned values by SHA-256, and on a default reappearing in config.",
    "",
    "  My first version of it used plain 'git ls-files'. I planted the retired",
    "  key in a new script, ran the suite, and it PASSED: the file was untracked,",
    "  so the check never saw it. It would have gone red in CI one commit too",
    "  late, which is the same 'found it afterwards' the test exists to end.",
    "  '--others --exclude-standard' is the load-bearing half.",
    "",
    "GATE  SQLite 1436 passed / 0 skipped / 0 warnings",
    "      Postgres 1436 passed / 0 skipped / 0 warnings",
    "      ruff clean. Baseline was 1428; +8 is 4 auth tests and 4 guard tests.",
    "",
    "STILL TRUE AFTER THIS COMMIT",
    "  Nothing is rotated until AGENT_API_KEY and AUTH_PASSWORD are set on",
    "  Railway. This commit makes rotation possible; it does not perform it."
)

$didCommit = New-Commit -Files $files -Message $msg
if (-not $didCommit) {
    Write-Host "Nothing to commit. Already shipped?" -ForegroundColor Yellow
}

Push-AndWatch

# ---------------------------------------------------------------------------
# 4. The part that actually rotates anything. It is not automatable from here.
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=====================================================================" -ForegroundColor Yellow
Write-Host " THE CODE IS DEPLOYED. NOTHING IS ROTATED YET." -ForegroundColor Yellow
Write-Host "=====================================================================" -ForegroundColor Yellow
Write-Host ""

if ($newKey) {
    Write-Host " On Railway, set these two variables on the service and redeploy:" -ForegroundColor White
    Write-Host ""
    Write-Host "   AGENT_API_KEY   $newKey" -ForegroundColor Green
    Write-Host "   AUTH_PASSWORD   $newPassword" -ForegroundColor Green
    Write-Host ""
    Write-Host " The key is already in scripts\.agent-key on this machine, so your" -ForegroundColor DarkGray
    Write-Host " scripts will pick it up. The password is shown once, here. Put it" -ForegroundColor DarkGray
    Write-Host " in your password manager now - nothing else stores it, and there" -ForegroundColor DarkGray
    Write-Host " is no way to read it back out of the app." -ForegroundColor DarkGray
} else {
    Write-Host " Run without -NoGenerate, or generate your own - see ROTATION.md." -ForegroundColor White
}

Write-Host ""
Write-Host " Then verify:" -ForegroundColor White
Write-Host "   .\scripts\smoke\smoke-the-published-credentials-are-dead.ps1" -ForegroundColor White
Write-Host ""
Write-Host " Until you do, the old key still works - it is still what" -ForegroundColor Yellow
Write-Host " AGENT_API_KEY is set to. That window is the only reason to hurry." -ForegroundColor Yellow
Write-Host ""
Write-Host " Full runbook: ROTATION.md" -ForegroundColor DarkGray
Write-Host ""

# ship-pr80-rule-params-migration.ps1
# ship 2026-09-29 - PR 80: migration 0025 adds trading_rules.params (nullable
# JSON), ALONE, before any code reads it (PR 47 lesson). Railway's pre-deploy
# 'alembic upgrade head' applies it; this script then checks /health reports
# schema_revision 0025_rule_params. Built on 9479ffb (PR 79).
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"

$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$base = 'https://investwise-pro-production.up.railway.app'
Set-Location $repo

$subject = 'PR 80: migration 0025 - trading_rules.params (add-only)'

if ((git log --oneline -1 --format=%s).Trim() -eq $subject) {
    Write-Host 'PR 80 already landed - nothing to do.' -ForegroundColor Yellow
    return
}

Write-Host '=== 1. sync check ===' -ForegroundColor Cyan
Remove-Item (Join-Path $repo '.git\index.lock') -Force -ErrorAction SilentlyContinue
git fetch origin
if ($LASTEXITCODE -ne 0) { throw 'git fetch failed' }
$sb = (git status -sb | Select-Object -First 1)
Write-Host "  $sb"
if ($sb -match 'behind') { throw 'local main is behind origin - pull/rebase first' }
$head = (git rev-parse --short HEAD).Trim()
if ($head -ne '9479ffb') {
    Write-Host "  HEAD is $head, the patch was built on 9479ffb." -ForegroundColor Yellow
    Write-Host '  The apply check below decides; this is only a warning.' -ForegroundColor Yellow
}

Write-Host '=== 2. apply the patch ===' -ForegroundColor Cyan
$patch = Join-Path $repo 'pr80-rule-params.patch'
if (-not (Test-Path $patch)) { throw "patch not found: $patch" }
$eap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
git apply --check --reverse $patch 2>&1 | Out-Null
$reverseOk = ($LASTEXITCODE -eq 0)
git apply --check $patch 2>&1 | Out-Null
$forwardOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $eap
if ($reverseOk) {
    Write-Host '  already applied in full - skipping.' -ForegroundColor Yellow
} else {
    if (-not $forwardOk) { throw 'the PR 80 patch does not apply cleanly - stop here' }
    git apply $patch
    if ($LASTEXITCODE -ne 0) { throw 'the PR 80 patch failed to apply' }
    Write-Host '  applied.' -ForegroundColor Green
}
if ((Get-Content (Join-Path $repo 'STATUS.md') -Raw) -notmatch '## PR 80 ') {
    throw 'STATUS.md has no PR 80 section - the patch did not land fully'
}

Write-Host '=== 3. focused gate + ruff ===' -ForegroundColor Cyan
python -m pytest -q tests/test_startup_ddl_covers_migrations.py
if ($LASTEXITCODE -ne 0) { throw 'focused tests FAILED - not committing' }
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff FAILED - CI gates on this' }
Write-Host '  Green.' -ForegroundColor Green

Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$files = @(
    'alembic/versions/0025_rule_params.py',
    'app/models/tables.py',
    'app/main.py',
    'scripts/deploy/ship-pr80-rule-params-migration.ps1',
    'STATUS.md'
)
$msg = @(
    $subject,
    '',
    'Eran 2026-09-29, advanced-rules decision 1: a small add-only migration.',
    'One nullable JSON column for the advanced rules second settings; null',
    'means house defaults. Guarded like 0021-0024, mirrored into main.py.',
    'Ships alone before PR 81 reads it (PR 47). Sandbox, Postgres 16: fresh',
    'upgrade, 0024->0025 on a table without the column, downgrade, re-upgrade.',
    '',
    'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>',
    'Claude-Session: https://claude.ai/code/session_01Kunu6tAS8L56Fna5wnDrin'
)
if (-not (New-Commit -Files $files -Message $msg)) { throw 'nothing committed' }
$sha = (git rev-parse --short HEAD).Trim()
Write-Host "  committed $sha" -ForegroundColor Green

Write-Host '=== 5. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha -BaseUrl $base

Write-Host '=== 6. schema check ===' -ForegroundColor Cyan
$hb = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 60
Write-Host "  commit $($hb.commit)  schema_revision $($hb.schema_revision)"
if ($hb.schema_revision -ne '0025_rule_params') { throw 'schema_revision is not 0025_rule_params - STOP, do not ship PR 81' }
Remove-Item $patch -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "PR 80 live as $sha with schema 0025. Tell Claude 'shipped'." -ForegroundColor Green

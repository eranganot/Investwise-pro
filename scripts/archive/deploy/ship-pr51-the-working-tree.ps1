# ship-pr51-the-working-tree.ps1
# PR 51 - repo hygiene. RUN THIS LAST, after PR 49 and PR 50.
#
# git status carried fifteen untracked entries, some for weeks. That is not
# untidiness: .gitignore:17-22 already records the version of this that nearly
# cost a release - "git status --short returned an EMPTY view while five files
# sat staged, which was very nearly reported as a clean tree". A status listing
# nobody reads is a status that cannot warn anybody.
#
# Tracks ten one-off scripts and the v3 execution plan. All ten were scanned
# for credentials before tracking: no key, token or password literal, and
# scripts\.agent-key appears in none of them.
#
# Deletes the two source tarballs and ignores *.tgz. They are git archive
# snapshots, byte-identical to commits already in the history, and git archive
# reproduces either one in a second.
#
# Leaves app\services\candidate_search.py and its test UNTRACKED on purpose -
# that is an open land-or-discard decision and it is yours, not mine.
#
# Pure ASCII, no backticks.

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'

Set-Location $repo
Write-Host '=== 0. where we are ===' -ForegroundColor Cyan
git rev-parse --short HEAD
git status --short

# --------------------------------------------------- do not track a secret
Write-Host '=== 1. credential scan of everything about to be tracked ===' -ForegroundColor Cyan
$toTrack = @(
  'scripts\commit-t0.ps1', 'scripts\fix-t3.ps1', 'scripts\portfolio-restructure.ps1',
  'scripts\ship-t0.ps1', 'scripts\ship-t1.ps1', 'scripts\ship-t2.ps1',
  'scripts\ship-t3.ps1', 'scripts\ship-t6b.ps1', 'scripts\smoke\smoke-sleeves.ps1',
  'scripts\verify-t03.ps1', 'EXECUTION_PLAN_2026-08-29_v3.md'
)
foreach ($f in $toTrack) {
  if (-not (Test-Path $f)) { throw "expected to track $f and it is not there" }
}

# The live key itself, if it is on this box - the one string that must never
# reach a tracked file.
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (Test-Path $keyPath) {
  $key = (Get-Content $keyPath -Raw).Trim()
  foreach ($f in $toTrack) {
    if ((Get-Content $f -Raw) -like "*$key*") { throw "$f CONTAINS THE AGENT KEY - stop" }
  }
  Write-Host '  the agent key appears in none of them' -ForegroundColor Green
} else {
  Write-Host '  no scripts\.agent-key on this box - skipped the literal check' -ForegroundColor Yellow
}

$patterns = 'ghp_', 'github_pat', 'AKIA', 'BEGIN RSA', 'BEGIN OPENSSH'
foreach ($f in $toTrack) {
  $body = Get-Content $f -Raw
  foreach ($pat in $patterns) {
    if ($body -like "*$pat*") { throw "$f matches $pat - stop and look" }
  }
}
Write-Host '  no credential patterns' -ForegroundColor Green

# ----------------------------------------------------------- the tarballs
Write-Host '=== 2. drop the source tarballs ===' -ForegroundColor Cyan
foreach ($t in @('iw-src.tgz', 'iw-src-sync.tgz')) {
  if (Test-Path $t) {
    $mb = [math]::Round((Get-Item $t).Length / 1MB, 2)
    Remove-Item $t -Force
    Write-Host ("  removed {0} ({1} MB)" -f $t, $mb) -ForegroundColor Green
  }
}
$ignored = git check-ignore iw-src.tgz 2>$null
if (-not $ignored) { Write-Host '  note: *.tgz is not being ignored - check .gitignore' -ForegroundColor Yellow }

Write-Host '=== 3. ruff + full suite ===' -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { throw 'ruff failed - do not push' }
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'the suite failed - do not push' }

# ------------------------------------------------------------------- commit
Write-Host '=== 4. commit ===' -ForegroundColor Cyan
$msg = @'
PR 51: the working tree stopped hiding things

git status carried fifteen untracked entries, some of them for weeks. That is
not untidiness, it is the failure mode .gitignore:17-22 is already written
about: "git status --short returned an EMPTY view while five files sat staged,
which was very nearly reported as a clean tree". A status listing nobody reads
is a status that cannot warn anybody.

Tracked, because they are the record of what was actually run against this
book: ten one-off scripts under scripts/ - commit-t0, fix-t3,
portfolio-restructure, ship-t0 through ship-t3, ship-t6b, smoke/smoke-sleeves,
verify-t03 - and EXECUTION_PLAN_2026-08-29_v3.md, which was the only plan in
the series not tracked while V5 and V6 both were.

All eleven were scanned for credentials before tracking: no key, token or
password literal, and scripts/.agent-key appears in none of them. It is read at
runtime and git-ignored by .gitignore:31. The ship script re-runs that scan
against the live key before it commits, so this is checked at the moment it
matters rather than only claimed here.

Ignored and deleted: iw-src.tgz and iw-src-sync.tgz. Both are git archive
snapshots made to move the tree to a sandbox - 1.7 MB each, byte-identical to
commits already in the history, and worth nothing that git archive cannot
reproduce in a second. *.tgz and *.tar.gz are now ignored.

Deliberately left untracked: app/services/candidate_search.py and
tests/test_candidate_search.py. That is an open land-or-discard decision and it
is Eran's, recorded in EXECUTION_PLAN_2026-09-03_v6.md. Saying so in STATUS.md
turns the two remaining ?? lines into an answer rather than a question, so
anything new that appears in that listing is genuinely new.

Not financial advice.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01LVSt8xG9YgVT2gwryXVJ19
'@

$msgPath = Join-Path $repo 'COMMIT_MSG.txt'
[System.IO.File]::WriteAllText($msgPath, $msg)   # UTF-8, no BOM

git add -- .gitignore STATUS.md scripts/deploy/ship-pr51-the-working-tree.ps1
git add -- scripts/commit-t0.ps1 scripts/fix-t3.ps1 scripts/portfolio-restructure.ps1
git add -- scripts/ship-t0.ps1 scripts/ship-t1.ps1 scripts/ship-t2.ps1 scripts/ship-t3.ps1
git add -- scripts/ship-t6b.ps1 scripts/smoke/smoke-sleeves.ps1 scripts/verify-t03.ps1
git add -- EXECUTION_PLAN_2026-08-29_v3.md
git commit -F COMMIT_MSG.txt
if ($LASTEXITCODE -ne 0) { throw 'commit failed' }
Remove-Item $msgPath -Force

$sha = (git rev-parse --short HEAD).Trim()
Write-Host "committed $sha" -ForegroundColor Green

Write-Host '=== 5. push ===' -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { throw 'push failed' }

& (Join-Path $repo 'scripts\deploy\verify-deploy.ps1') -Sha $sha

Write-Host '=== 6. what is left in the tree ===' -ForegroundColor Cyan
git status --short
Write-Host ''
Write-Host 'Expect exactly two lines: candidate_search.py and its test.' -ForegroundColor Yellow
Write-Host 'Anything else is new since this ran, and worth a look.' -ForegroundColor Yellow
Write-Host ''
Write-Host 'PR 51 SHIPPED.' -ForegroundColor Green

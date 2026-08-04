# PHASE 16 - stop PowerShell scripts showing as whole-file rewrites; drop a
#            renamed smoke script that was left tracked.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\scripts\deploy\phase16-repo-hygiene.ps1
#
# No app code changes. No migration. Nothing to QA.
#
# Two bits of debris from the phase 10-15 run:
#
#   1. Every .ps1 reported ~190 changed lines in `git diff` for files nobody had
#      edited: they are written CRLF on Windows, git had stored them LF. A real
#      one-line change is invisible inside that noise, and it is part of why
#      `git add -A` is banned here. .gitattributes now pins the policy.
#   2. smoke-phase10-13.ps1 was renamed to smoke-phase10-15.ps1 as the smoke
#      grew to cover phases 14 and 15. The new name was committed; the old path
#      was never removed, so it is still tracked while absent from disk.

. "$PSScriptRoot\_common.ps1"
Enter-Repo

Write-Host "`nStaging the renamed smoke script's old path..." -ForegroundColor Cyan
git rm --quiet --ignore-unmatch -- "scripts/smoke/smoke-phase10-13.ps1"

Write-Host "Applying the line-ending policy..." -ForegroundColor Cyan
git add -- ".gitattributes" "scripts/deploy/phase16-repo-hygiene.ps1"

# Renormalise ONLY the scripts directory. `git add --renormalize .` would sweep
# the whole tree including the tracked frontend/node_modules, which is exactly
# the CRLF-noisy blast radius this repo already avoids.
git add --renormalize -- "scripts"

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing staged - already clean." -ForegroundColor Yellow
    exit 0
}
Write-Host "`nStaged:" -ForegroundColor Cyan
git diff --cached --stat

# The suite cannot be affected by any of this, but the house rule is that
# nothing is pushed without it being green.
Invoke-Suite

$msgPath = Join-Path $RepoRoot "COMMIT_MSG.txt"
Set-Content -Path $msgPath -Encoding utf8 -Value @(
    "chore: pin line endings; drop the renamed smoke script's old path",
    "",
    "Every .ps1 was reporting ~190 changed lines in git diff for files",
    "nobody had edited - written CRLF on Windows, stored LF by git. A real",
    "one-line change is invisible inside that, and the noise is part of why",
    "`git add -A` is banned in this repo.",
    "",
    ".gitattributes now normalises text to LF in the repository and checks",
    "PowerShell and batch files out as CRLF, which is what the Windows",
    "shells expect. Renormalised the scripts directory only: a tree-wide",
    "renormalise would sweep the tracked frontend/node_modules, which is the",
    "blast radius this repo already goes out of its way to avoid.",
    "",
    "Also removes scripts/smoke/smoke-phase10-13.ps1, renamed to",
    "smoke-phase10-15.ps1 when the smoke grew to cover phases 14 and 15.",
    "The new name was committed; the old path stayed tracked while absent",
    "from disk, so a stale file list made a re-run of phase 14 fail with",
    "'Missing files' rather than saying it had already committed."
)
git commit -F $msgPath
if ($LASTEXITCODE -ne 0) { Write-Host "Commit failed." -ForegroundColor Red; exit 1 }
Remove-Item $msgPath -ErrorAction SilentlyContinue

Push-AndWatch

Write-Host ''
Write-Host 'git status should now be clean apart from real edits.' -ForegroundColor Cyan
Write-Host 'Re-running an already-committed phase script is safe - it will say' -ForegroundColor DarkGray
Write-Host '"Nothing staged - already committed?" instead of failing.' -ForegroundColor DarkGray

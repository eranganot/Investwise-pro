# SMOKE - O6 (D143): CI is test-postgres + lint only; no SQLite job.
#
#   .\scripts\smoke\smoke-o6-ci-postgres-only.ps1 -Sha <short sha of the merge>
#
#   1  the CI run for the served commit (push to main) ran exactly test-postgres and lint, both green
#   2  NOT CHANGED: the app answers /health ok on the same schema (no app code in this change)
# READ-ONLY (GETs only). ASCII ONLY. Reads the public GitHub API; set GITHUB_TOKEN to avoid its rate limit.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [string]$Repo = "eranganot/investwise-pro")
. "$PSScriptRoot\_smoke-common.ps1"
Start-Smoke "O6"
Assert-Build -Sha $Sha -Schema '0029_drop_legacy_tables' -Why "(no migration in this change)"

$gh = @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'investwise-smoke' }
if ($env:GITHUB_TOKEN) { $gh['Authorization'] = "Bearer $($env:GITHUB_TOKEN)" }

Sec "1  the CI run for this commit"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
$full = if ($h) { "$($h.commit)" } else { "$Sha" }
$runs = $null
try { $runs = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/actions/runs?branch=main&event=push&per_page=30" -Headers $gh -TimeoutSec 60 } catch { Bad "GitHub API did not answer: $($_.Exception.Message)" }
if ($runs) {
    $run = @($runs.workflow_runs | Where-Object { $_.head_sha -like "$full*" -and $_.name -eq 'CI' })[0]
    if (-not $run) { Skip "no CI run found for $full yet - re-run in a few minutes" }
    elseif ($run.status -ne 'completed') { Skip "CI run $($run.id) is $($run.status) - re-run when it finishes (~15 min)" }
    else {
        $jobs = $null
        try { $jobs = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/actions/runs/$($run.id)/jobs" -Headers $gh -TimeoutSec 60 } catch { Bad "jobs did not answer: $($_.Exception.Message)" }
        if ($jobs) {
            $names = @($jobs.jobs | ForEach-Object { $_.name } | Sort-Object)
            Note ("jobs: {0}" -f ($names -join ', '))
            if (($names -join ',') -eq 'lint,test-postgres') { Ok "exactly test-postgres and lint ran" } else { Bad "expected lint + test-postgres, got: $($names -join ', ')" }
            if ($names -contains 'test') { Bad "the SQLite 'test' job still ran" } else { Ok "no SQLite job" }
            $red = @($jobs.jobs | Where-Object { $_.conclusion -ne 'success' })
            if ($red.Count -eq 0) { Ok "every job green ($($run.conclusion))" } else { Bad ("not green (an incident: the build is live): {0}" -f (($red | ForEach-Object { "$($_.name)=$($_.conclusion)" }) -join ', ')) }
        }
    }
}

Sec "2  NOT CHANGED"
if ($h -and $h.status -eq 'ok') { Ok "/health ok" } else { Bad "/health not ok" }
Stop-Smoke

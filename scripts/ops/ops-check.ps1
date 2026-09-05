# ops-check.ps1 - what state the ops items are actually in, right now.
#
# The ops list has been the oldest thing on the board for weeks, and part of
# the reason is that "rotate the tokens" is not checkable: you do it, and then
# nothing tells you it is done. This makes each item a question with an answer.
#
# READ-ONLY. It changes nothing, rotates nothing and prints no secret. Run it
# before the runbook to see what is outstanding, and after to confirm.
#
# Each item reports DONE / TODO / MANUAL / UNKNOWN:
#   DONE     verified from an API this script can reach
#   TODO     verified as NOT done
#   MANUAL   only you can see it (a dashboard field, a rotation date)
#   UNKNOWN  a check that needs a credential this script was not given
#
# Optional, and never required:
#   $env:GITHUB_TOKEN   a PAT with repo scope, to check branch protection
#
# Pure ASCII, no backticks.

param(
  [string]$Repo    = 'C:\dev\Investwise-pro',
  [string]$BaseUrl = 'https://investwise-pro-production.up.railway.app',
  [string]$GitHubRepo = 'eranganot/Investwise-pro'
)

$ErrorActionPreference = 'Stop'
$script:Items = @()

function Item {
  param([string]$Id, [string]$What, [string]$Status, [string]$Detail = '', [string]$Next = '')
  $colour = switch ($Status) {
    'DONE'   { 'Green' }
    'TODO'   { 'Red' }
    'MANUAL' { 'Yellow' }
    default  { 'DarkGray' }
  }
  Write-Host ("  [{0,-7}] {1}" -f $Status, $What) -ForegroundColor $colour
  if ($Detail) { Write-Host ("            {0}" -f $Detail) -ForegroundColor DarkGray }
  if ($Next)   { Write-Host ("            -> {0}" -f $Next) -ForegroundColor DarkGray }
  $script:Items += [pscustomobject]@{ Id = $Id; What = $What; Status = $Status }
}

Write-Host ''
Write-Host '=== ops check ===' -ForegroundColor Cyan
Write-Host ''

# ---------------------------------------------------------------- 1. branch
Write-Host '1. branch protection on main' -ForegroundColor Cyan
if (-not $env:GITHUB_TOKEN) {
  Item 'branch' 'main is protected' 'UNKNOWN' 'no GITHUB_TOKEN in this shell, so the API cannot be asked'  'set $env:GITHUB_TOKEN, or check github.com/eranganot/Investwise-pro/settings/branches'
} else {
  $h = @{ Authorization = "Bearer $env:GITHUB_TOKEN"; 'User-Agent' = 'iw-ops-check';
          Accept = 'application/vnd.github+json' }
  try {
    $p = Invoke-RestMethod -Uri "https://api.github.com/repos/$GitHubRepo/branches/main/protection" -Headers $h -TimeoutSec 30
    $bits = @()
    if ($p.required_pull_request_reviews) { $bits += 'PR review required' }
    if ($p.required_status_checks)        { $bits += 'status checks required' }
    if ($p.allow_force_pushes.enabled -eq $false) { $bits += 'force-push blocked' }
    Item 'branch' 'main is protected' 'DONE' ($bits -join ', ')
  } catch {
    if ("$_" -match '404') {
      Item 'branch' 'main is protected' 'TODO' 'the API says no protection rule exists'  'Settings > Branches > Add rule for main. Two minutes, zero risk, and the only ops item that prevents a class of incident rather than reducing an exposure.'
    } else {
      Item 'branch' 'main is protected' 'UNKNOWN' "$_"
    }
  }
}

# -------------------------------------------------------------- 2. the keys
Write-Host ''
Write-Host '2. the agent key' -ForegroundColor Cyan
$keyPath = Join-Path $Repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) {
  Item 'agentkey' 'the agent key is present and git-ignored' 'TODO' 'scripts\.agent-key does not exist'
} else {
  $ignored = git -C $Repo check-ignore 'scripts/.agent-key' 2>$null
  if ($ignored) {
    $age = [int]((Get-Date) - (Get-Item $keyPath).LastWriteTime).TotalDays
    Item 'agentkey' 'the agent key is present and git-ignored' 'DONE' ("local copy last written {0} days ago" -f $age)
  } else {
    Item 'agentkey' 'the agent key is present and git-ignored' 'TODO' 'it is NOT ignored - it can be committed' 'add scripts/.agent-key* to .gitignore'
  }
  $apiHeaders = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }
}

# --------------------------------------------------------------- 3. VAPID
Write-Host ''
Write-Host '3. the push keypair' -ForegroundColor Cyan
if (-not $apiHeaders) {
  Item 'vapid' 'the VAPID pair survives a database rebuild' 'UNKNOWN' 'no agent key, so /push/status cannot be read'
} else {
  try {
    $st = Invoke-RestMethod -Uri "$BaseUrl/api/v1/push/status" -Headers $apiHeaders -TimeoutSec 30
    if ($st.vapid_pinned_by_env -and $st.vapid_env_matches_db -eq $true) {
      Item 'vapid' 'the VAPID pair survives a database rebuild' 'DONE' ("pinned and matching; {0} subscription(s)" -f $st.subscriptions)
    } elseif ($st.vapid_pinned_by_env -and $st.vapid_env_matches_db -ne $true) {
      Item 'vapid' 'the VAPID pair survives a database rebuild' 'TODO'  'PINNED BUT MISMATCHED - every existing subscription is unreachable and pushes report as sent'  'remove both VAPID variables in Railway NOW, then redo the pin with scripts\ops\verify-vapid-pin.ps1'
    } else {
      Item 'vapid' 'the VAPID pair survives a database rebuild' 'TODO'  ("not pinned; the pair exists only as a kv_settings row. {0} subscription(s) at risk if that database is ever rebuilt." -f $st.subscriptions)  'scripts\ops\verify-vapid-pin.ps1 -Before'
    }
  } catch {
    Item 'vapid' 'the VAPID pair survives a database rebuild' 'UNKNOWN' "$_"
  }
}

# ------------------------------------------------------ 4. the public db url
Write-Host ''
Write-Host '4. a database URL that resolves from outside Railway' -ForegroundColor Cyan
$mentions = @(git -C $Repo grep -l 'DATABASE_PUBLIC_URL' -- '*.py' '*.md' '*.ps1' 2>$null)
if ($mentions.Count -gt 0) {
  Item 'dbpublic' 'DATABASE_PUBLIC_URL is known to this repo' 'DONE' ($mentions -join ', ')
} else {
  Item 'dbpublic' 'DATABASE_PUBLIC_URL is known to this repo' 'MANUAL'  'nothing in the repo mentions it. Railway Postgres normally exposes one.'  'Railway > the Postgres service > Variables. If it is there, alembic and the kv_settings rows become reachable from this laptop - which is the constraint the whole migration saga was built around.'
}

# ------------------------------------------------------------- 5. rotations
Write-Host ''
Write-Host '5. credential rotations' -ForegroundColor Cyan
Item 'ghtoken' 'the GitHub token has been rotated' 'MANUAL'  'no API reports a token age'  'github.com/settings/tokens - regenerate, then re-authenticate git push once'
Item 'railwaytoken' 'the Railway token has been rotated' 'MANUAL'  'no API reports a token age'  'Railway > Account > Tokens'
Item 'pgpassword' 'the Postgres password has been rotated' 'MANUAL'  'RECOMMENDED NOT NOW - see the runbook'  'the only item where the rotation is likelier to cause an incident than the risk it removes: never exposed, wired through Railway variable references, and a wrong move takes the database offline. Rotate on a reason, not a schedule.'

# --------------------------------------------------------------- 6. deploy
Write-Host ''
Write-Host '6. the deploy gate (PR 49/52), because ops is not only credentials' -ForegroundColor Cyan
try {
  $deploy = (Get-Content (Join-Path $Repo 'railway.json') -Raw | ConvertFrom-Json).deploy
  if ($deploy.healthcheckPath) {
    Item 'healthcheck' 'a container that cannot serve never takes traffic' 'DONE' ("healthcheckPath {0}, timeout {1}s" -f $deploy.healthcheckPath, $deploy.healthcheckTimeout)
  } else {
    Item 'healthcheck' 'a container that cannot serve never takes traffic' 'TODO' 'no healthcheckPath in railway.json'
  }
  if ($deploy.preDeployCommand -like '*alembic*') {
    Item 'predeploy' 'the migrations run on deploy' 'DONE' $deploy.preDeployCommand
  } else {
    Item 'predeploy' 'the migrations run on deploy' 'TODO' 'nothing runs alembic against production'
  }
} catch {
  Item 'healthcheck' 'railway.json is readable' 'UNKNOWN' "$_"
}
try {
  $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30
  if ($h.schema_revision) {
    Item 'stamped' 'the database is stamped at a revision' 'DONE' ("schema_revision {0}, serving {1}" -f $h.schema_revision, $h.commit)
  } else {
    Item 'stamped' 'the database is stamped at a revision' 'TODO' 'schema_revision is null'
  }
} catch {
  Item 'stamped' 'production answers' 'UNKNOWN' "$_"
}

# -------------------------------------------------------------- summary
$done    = @($script:Items | Where-Object { $_.Status -eq 'DONE' })
$todo    = @($script:Items | Where-Object { $_.Status -eq 'TODO' })
$manual  = @($script:Items | Where-Object { $_.Status -eq 'MANUAL' })
$unknown = @($script:Items | Where-Object { $_.Status -eq 'UNKNOWN' })

Write-Host ''
Write-Host ("=== {0} done, {1} to do, {2} manual, {3} unknown ===" -f $done.Count, $todo.Count, $manual.Count, $unknown.Count) -ForegroundColor Cyan
foreach ($i in $todo)    { Write-Host ("  TODO    {0}" -f $i.What) -ForegroundColor Red }
foreach ($i in $manual)  { Write-Host ("  MANUAL  {0}" -f $i.What) -ForegroundColor Yellow }
foreach ($i in $unknown) { Write-Host ("  UNKNOWN {0}" -f $i.What) -ForegroundColor DarkGray }
Write-Host ''
Write-Host 'The order that matters is in scripts\ops\OPS_RUNBOOK.md.' -ForegroundColor Cyan

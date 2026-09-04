# verify-deploy.ps1 - did the thing we just pushed actually come up?
#
# Every ship script has re-derived this loop, and PR 47 is why it now lives in
# one file. Railway's dashboard said "Deployment successful" through a two-hour
# total outage, because that card reflects build-and-deploy and not whether
# anything answers. The only honest signal in that whole episode was a ship
# script's own /health wait.
#
# railway.json now sets healthcheckPath, so Railway itself holds the old
# container until the new one serves 2xx on /health/ready and fails the deploy
# otherwise. This script is the second half of that: it confirms from OUTSIDE
# that the commit you pushed is the commit answering, which the healthcheck
# cannot tell you - a healthcheck passing on the PREVIOUS container looks
# identical from here.
#
# Usage:
#   .\verify-deploy.ps1 -Sha (git rev-parse --short HEAD)
#   .\verify-deploy.ps1 -Sha abc1234 -TimeoutMinutes 12
#
# Exit: throws if the pushed commit is not serving within the window. Callers
# should NOT catch it - a ship script that reports success on an unverified
# deploy is the failure mode this exists to remove.
#
# Pure ASCII, no backticks.

param(
  [Parameter(Mandatory = $true)][string]$Sha,
  [string]$BaseUrl = 'https://investwise-pro-production.up.railway.app',
  [int]$TimeoutMinutes = 12,
  [int]$PollSeconds = 20
)

$ErrorActionPreference = 'Stop'
$Sha = $Sha.Trim()

Write-Host ''
Write-Host ("=== verifying {0} is the commit that answers ===" -f $Sha) -ForegroundColor Cyan

$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
$live = $null
$sawOther = $null

while ((Get-Date) -lt $deadline) {
  try {
    $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 20
    if ($h.commit -eq $Sha) { $live = $h; break }
    $sawOther = $h.commit
    Write-Host ("  serving {0}, waiting for {1}..." -f $h.commit, $Sha)
  } catch {
    Write-Host '  no answer yet...'
  }
  Start-Sleep -Seconds $PollSeconds
}

if ($null -eq $live) {
  Write-Host ''
  Write-Host 'NOT VERIFIED.' -ForegroundColor Red
  if ($null -ne $sawOther) {
    Write-Host ("  Production answers, but on {0} - your commit never took over." -f $sawOther) -ForegroundColor Red
    Write-Host '  With healthcheckPath set that is the SAFE outcome: the new container' -ForegroundColor Yellow
    Write-Host '  failed its healthcheck and Railway kept the working one. Open the' -ForegroundColor Yellow
    Write-Host '  failed deploy and read its log - the reason is in there, and unlike' -ForegroundColor Yellow
    Write-Host '  PR 47 the site is still up while you read it.' -ForegroundColor Yellow
  } else {
    Write-Host '  Production does not answer at all. This is an outage, not a slow deploy.' -ForegroundColor Red
  }
  throw "$Sha was not serving within $TimeoutMinutes minutes"
}

Write-Host ("  LIVE: {0} v{1} ({2})" -f $live.commit, $live.version, $live.environment) -ForegroundColor Green

# The readiness probe, which is also what Railway gated the promotion on. Asked
# again from outside so the result is in the ship log rather than only Railway's.
try {
  $r = Invoke-RestMethod -Uri "$BaseUrl/health/ready" -TimeoutSec 20
  Write-Host ("  ready: {0}, database {1}" -f $r.status, $r.database) -ForegroundColor Green
} catch {
  throw "/health/ready did not answer 200 on $Sha - the database is not reachable from the app"
}

$rev = $live.schema_revision
if ($null -eq $rev) {
  Write-Host '  schema_revision: null - the migrations have never run against this database' -ForegroundColor Yellow
} else {
  Write-Host ("  schema_revision: {0}" -f $rev) -ForegroundColor Green
}

Write-Host ''
return $live

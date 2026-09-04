# verify-vapid-pin.ps1 - check the VAPID pin before and after, without ever
# touching the private key.
#
# WHY THIS EXISTS. The push keypair lives in one place: a kv_settings row in
# production's database, minted lazily on first use. There is exactly ONE live
# subscription bound to it. If that database is ever rebuilt or restored, the
# app mints a NEW pair, the old subscription is bound to a key nobody holds any
# more, and every push after that reports as sent and arrives nowhere. The phone
# just goes quiet.
#
# Pinning the pair into Railway env vars fixes that. But get_vapid() in
# push_service.py carries the warning that matters, in its own words:
#
#   "PINNING THE WRONG PAIR IS SILENT, AND IT KILLS EVERY SUBSCRIPTION."
#
# So the pin is only safe if the pair you pin is the pair the database already
# minted - and the only way to know is to compare. The app does that compare
# itself and publishes the answer at /api/v1/push/status as vapid_env_matches_db.
# This script reads it, before and after.
#
# WHAT THIS SCRIPT DELIBERATELY DOES NOT DO: read, print, store or transmit the
# private key. It is a secret with no backup; it goes from the database straight
# into the Railway variable field and nowhere else - not a file, not a
# screenshot, not a chat window.
#
# THE PROCEDURE
#   1. Run this with -Before. It records the current public key and subscription
#      count.
#   2. Get the pair out of the database. Railway dashboard -> the Postgres
#      service -> Data (or "railway connect Postgres"), then:
#
#          SELECT key, value FROM kv_settings WHERE key LIKE 'vapid%';
#
#      Two rows: vapid_public_key and vapid_private_key.
#   3. Check the public key it shows matches the one this script printed in
#      step 1. If it does not, STOP - you are looking at a different database.
#   4. In the Railway dashboard, on the APP service (not Postgres), add:
#          VAPID_PUBLIC_KEY   = <the vapid_public_key value>
#          VAPID_PRIVATE_KEY  = <the vapid_private_key value>
#      Railway redeploys on a variable change. With healthcheckPath set (PR 49)
#      a container that cannot serve never takes traffic, so a mistake here is a
#      failed deploy rather than an outage.
#   5. Run this with -After. It must report pinned=True and matches=True.
#   6. Run this with -After -TestPush to fire one push and confirm the phone
#      still receives it. That is the only proof that survives.
#
# IF STEP 5 SAYS matches=False: remove both variables in Railway immediately.
# The app falls back to the database pair and the subscription is unharmed.
#
# Pure ASCII, no backticks.

param(
  [switch]$Before,
  [switch]$After,
  [switch]$TestPush,
  [string]$BaseUrl = 'https://investwise-pro-production.up.railway.app'
)

$ErrorActionPreference = 'Stop'
$repo = 'C:\dev\Investwise-pro'
$keyPath = Join-Path $repo 'scripts\.agent-key'
if (-not (Test-Path $keyPath)) { throw "need scripts\.agent-key to call the API" }
$h = @{ 'x-agent-key' = (Get-Content $keyPath -Raw).Trim() }

if (-not $Before -and -not $After) {
  Write-Host 'Pass -Before (record the current state) or -After (verify the pin).' -ForegroundColor Yellow
  Write-Host 'Read the header of this file first - the procedure is in it.' -ForegroundColor Yellow
  exit 1
}

$status = Invoke-RestMethod -Uri "$BaseUrl/api/v1/push/status" -Headers $h -TimeoutSec 30
$pk     = Invoke-RestMethod -Uri "$BaseUrl/api/v1/push/public-key" -Headers $h -TimeoutSec 30

Write-Host ''
Write-Host '=== push state ===' -ForegroundColor Cyan
Write-Host ("  subscriptions        : {0}" -f $status.subscriptions)
Write-Host ("  vapid_pinned_by_env  : {0}" -f $status.vapid_pinned_by_env)
Write-Host ("  vapid_env_matches_db : {0}" -f $status.vapid_env_matches_db)
Write-Host ("  push_library_ok      : {0}" -f $status.push_library_ok)
Write-Host ("  last_fanout_run      : {0}" -f $status.last_fanout_run)
Write-Host ''
Write-Host '  public key the app is serving to browsers (safe to display - it is'
Write-Host '  the applicationServerKey every subscriber already has):'
Write-Host ("    {0}" -f $pk.public_key) -ForegroundColor Green
Write-Host ''

if ($Before) {
  if ($status.subscriptions -lt 1) {
    Write-Host 'THERE IS NOTHING TO PROTECT: no live subscriptions.' -ForegroundColor Yellow
    Write-Host 'Pinning is still worth doing, but re-subscribe the phone first so' -ForegroundColor Yellow
    Write-Host 'step 6 can actually prove the pin did not break anything.' -ForegroundColor Yellow
  }
  if ($status.vapid_pinned_by_env) {
    Write-Host 'ALREADY PINNED. Nothing to do - jump to -After to re-verify.' -ForegroundColor Green
  } else {
    Write-Host 'NOT PINNED: the keypair exists only as a kv_settings row in' -ForegroundColor Yellow
    Write-Host 'production. Continue with step 2 in the header of this file.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Write down the public key above. In step 3 it must match the' -ForegroundColor Yellow
    Write-Host 'vapid_public_key row exactly, or you are on the wrong database.' -ForegroundColor Yellow
  }
  exit 0
}

# ---------------------------------------------------------------------- After
$ok = $true
if (-not $status.vapid_pinned_by_env) {
  Write-Host 'NOT PINNED. The variables are not set, or the service has not' -ForegroundColor Red
  Write-Host 'redeployed since they were. Check the deploy finished, then re-run.' -ForegroundColor Red
  $ok = $false
} elseif ($status.vapid_env_matches_db -ne $true) {
  Write-Host 'MISMATCH. The pinned public key is NOT the pair this database' -ForegroundColor Red
  Write-Host 'minted. Every existing subscription is bound to the old key and is' -ForegroundColor Red
  Write-Host 'now unreachable - pushes will report as sent and arrive nowhere.' -ForegroundColor Red
  Write-Host '' -ForegroundColor Red
  Write-Host 'REMOVE BOTH VARIABLES IN RAILWAY NOW. The app falls back to the' -ForegroundColor Red
  Write-Host 'database pair and the subscription is unharmed.' -ForegroundColor Red
  $ok = $false
} else {
  Write-Host 'PINNED AND MATCHING.' -ForegroundColor Green
  Write-Host '  The pair now survives a database rebuild, and it is the same pair'
  Write-Host '  every existing subscriber is bound to.'
}

if ($ok -and $TestPush) {
  Write-Host ''
  Write-Host '=== firing one test push ===' -ForegroundColor Cyan
  $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/push/test" -Method Post -Headers $h -TimeoutSec 60
  Write-Host ("  {0}" -f ($r | ConvertTo-Json -Compress))
  Write-Host ''
  Write-Host 'Now look at the phone. A push that reports as sent and does NOT' -ForegroundColor Yellow
  Write-Host 'arrive is the exact failure this whole procedure is about - the' -ForegroundColor Yellow
  Write-Host 'gateway rejects it, not the app, so the app cannot tell you.' -ForegroundColor Yellow
}

if (-not $ok) { exit 1 }

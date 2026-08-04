# Pull the traceback behind a 500 out of Railway.
#
#   .\scripts\get-error-log.ps1
#   .\scripts\get-error-log.ps1 -Lines 1000        # look further back
#   .\scripts\get-error-log.ps1 -Trigger           # fire the failing call first
#
# railway.json names no service, so this uses whatever service the directory is
# linked to. If the CLI says the project is not linked, run `railway link` once.
#
# Writes the full log to scripts/railway-error.log as well as printing the
# matches, so nothing is lost to console scrollback.

param(
    [int]$Lines = 500,
    [switch]$Trigger,
    # Which call to provoke before reading the log. Defaults to the endpoint
    # that was failing when this script was written; override to chase a
    # different 500, e.g. -TriggerPath '/api/v1/strategies/backtests/refresh'
    [string]$TriggerPath = "/api/v1/recommendations",
    [ValidateSet('GET', 'POST')][string]$TriggerMethod = "GET",
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app"
)

$ErrorActionPreference = 'Continue'
$out = Join-Path $PSScriptRoot "railway-error.log"

if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
    Write-Host "Railway CLI not found." -ForegroundColor Red
    Write-Host "  npm i -g @railway/cli   then   railway login   then   railway link" -ForegroundColor DarkGray
    Write-Host "Or read them in the browser: Railway -> your service -> Deployments -> Logs" -ForegroundColor DarkGray
    exit 1
}

if ($Trigger) {
    # Provoke the 500 immediately before reading, so the entry is at the tail.
    $h = @{ 'x-agent-key' = $(if ($env:IW_AGENT_KEY) { $env:IW_AGENT_KEY } else { "iwk_U8DOWb6g2mD--AP8EsEAqfbJVrp8aqF5oipOtVX5070" }) }
    Write-Host "Triggering $TriggerMethod $TriggerPath ..." -ForegroundColor Cyan
    try { Invoke-RestMethod -Method $TriggerMethod -Uri "$BaseUrl$TriggerPath" -Headers $h -TimeoutSec 900 | Out-Null }
    catch { Write-Host "  (it failed, as expected: $($_.Exception.Message))" -ForegroundColor DarkGray }
    Start-Sleep -Seconds 4
}

Write-Host "Fetching the last $Lines log lines..." -ForegroundColor Cyan
$log = & railway logs --lines $Lines 2>&1
if ($LASTEXITCODE -ne 0 -or -not $log) {
    Write-Host "railway logs failed. Output was:" -ForegroundColor Red
    $log | Select-Object -First 10
    Write-Host "If it says the project is not linked, run: railway link" -ForegroundColor DarkGray
    exit 1
}
$log | Set-Content -Path $out -Encoding utf8
Write-Host "Full log written to $out" -ForegroundColor DarkGray

# A traceback through Starlette is mostly middleware re-raising the same error,
# and the frames that identify the bug -- this app's own, and the exception line
# itself -- are at the very top and the very bottom. Printing a fixed window
# around the word "Traceback" showed neither: the first attempt returned thirty
# lines of starlette/middleware/base.py and nothing else. So filter for the two
# things that matter instead of slicing by position.

Write-Host "`n===== your app's frames =====" -ForegroundColor Cyan
$appFrames = $log | Select-String -Pattern '/code/app/|/app/services/|/app/api/|/app/engines/|/app/models/'
if ($appFrames) { $appFrames | Select-Object -Last 25 | ForEach-Object { $_.Line.Trim() } }
else { Write-Host "  (none - the error may be in a dependency or before the app was reached)" -ForegroundColor DarkGray }

Write-Host "`n===== exception lines =====" -ForegroundColor Cyan
# Matches "SomeError: message" / "sqlalchemy.exc.X" / asyncpg + friends, which
# is the line that actually names the fault.
$exc = $log | Select-String -Pattern '^\s*\w[\w\.]*(Error|Exception|Timeout)\b.*:|asyncpg\.|sqlalchemy\.exc\.|psycopg|OperationalError|UndefinedColumn|ProgrammingError'
if ($exc) { $exc | Select-Object -Last 15 | ForEach-Object { $_.Line.Trim() } }
else { Write-Host "  (none found - widen with -Lines 2000)" -ForegroundColor DarkGray }

Write-Host "`n===== last 40 lines, raw =====" -ForegroundColor Cyan
$log | Select-Object -Last 40 | ForEach-Object { $_ }

Write-Host "`nFull log: $out" -ForegroundColor DarkGray
Write-Host "Paste the 'your app frames' and 'exception lines' sections." -ForegroundColor Cyan

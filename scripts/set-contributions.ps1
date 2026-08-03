# Record what you have actually put in ("You put in ...").
#
# Only deposits and withdrawals may move this number. Trimming, selling and
# swapping rearrange money already inside the account and leave it alone.
#
# Set your total (replaces the ledger with one opening balance):
#   .\scripts\set-contributions.ps1 -Amount 20000
#
# Log a later transfer in or out (adds to the ledger):
#   .\scripts\set-contributions.ps1 -Amount 5000  -Mode adjust -Note 'March top-up'
#   .\scripts\set-contributions.ps1 -Amount -3000 -Mode adjust -Note 'withdrew'
#
# Just look at it, change nothing:
#   .\scripts\set-contributions.ps1 -Show
#
# Against a local dev server:
#   .\scripts\set-contributions.ps1 -Amount 20000 -BaseUrl 'http://127.0.0.1:8000'
#
# AUTH - production runs with REQUIRE_AUTH on, so an unauthenticated call gets
# 401. Easiest path is the agent key your app already supports (auth.py: a
# request carrying X-Agent-Key acts as SUPERADMIN without switching auth off).
# It is the AGENT_API_KEY variable in Railway. Put it in your shell once:
#
#   $env:IW_AGENT_KEY = '<the AGENT_API_KEY value from Railway>'
#   .\scripts\set-contributions.ps1 -Amount 20000
#
# The script reads $env:IW_AGENT_KEY automatically. Or pass -AgentKey / -Token
# explicitly. Prefer the env var so the secret stays out of your shell history.

param(
    [double]$Amount,
    [ValidateSet('set', 'adjust')][string]$Mode = 'set',
    [string]$Note = '',
    [string]$BaseUrl = 'https://investwise-pro-production.up.railway.app',
    [string]$Token = '',
    [string]$AgentKey = $env:IW_AGENT_KEY,
    [switch]$Show
)

$ErrorActionPreference = 'Stop'
$headers = @{ 'Content-Type' = 'application/json' }
if ($AgentKey) { $headers['X-Agent-Key'] = $AgentKey }
if ($Token) { $headers['Authorization'] = "Bearer $Token" }

function Show-State {
    $p = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio" -Headers $headers
    $src = if ($p.invested_source -eq 'contributions') { 'contributions ledger' }
           else { 'legacy cost-basis estimate (drifts - record a figure to fix)' }
    Write-Host ''
    Write-Host ("  You put in     {0:N2}" -f $p.invested_ils) -ForegroundColor Cyan
    Write-Host ("  Portfolio      {0:N2}" -f $p.nav_ils)
    Write-Host ("  Gain           {0:N2}  ({1}%)" -f $p.gain_ils, $p.gain_pct)
    Write-Host ("  Source         {0}" -f $src) -ForegroundColor DarkGray
    Write-Host ''
}

if ($Show) { Show-State; exit 0 }

if (-not $PSBoundParameters.ContainsKey('Amount')) {
    Write-Host "Pass -Amount (or -Show to just read it)." -ForegroundColor Yellow
    exit 1
}

$body = @{ amount_ils = $Amount; mode = $Mode; note = $Note } | ConvertTo-Json -Compress
Write-Host "POST $BaseUrl/api/v1/portfolio/contributions  $body" -ForegroundColor DarkGray

try {
    $r = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio/contributions" `
                           -Method Post -Headers $headers -Body $body
} catch {
    $code = $null
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    Write-Host "Request failed: $($_.Exception.Message)" -ForegroundColor Red
    switch ($code) {
        401 { Write-Host "Not authenticated. Production runs with REQUIRE_AUTH on." -ForegroundColor Yellow
              Write-Host "  Set the agent key once, then re-run:" -ForegroundColor DarkGray
              Write-Host "    `$env:IW_AGENT_KEY = '<AGENT_API_KEY from your Railway variables>'" -ForegroundColor DarkGray }
        403 { Write-Host "Authenticated but not permitted - the key or account lacks the ANALYST role." -ForegroundColor Yellow }
        404 { Write-Host "Route not found - the deploy has not landed yet." -ForegroundColor Yellow
              Write-Host "  Run scripts\deploy\phase10-contributions.ps1 first." -ForegroundColor DarkGray }
        500 { Write-Host "Server error - if the deploy just landed, the contributions table may be missing." -ForegroundColor Yellow
              Write-Host "  Run: alembic upgrade head" -ForegroundColor DarkGray }
    }
    exit 1
}

Write-Host ("Recorded. Total contributed: {0:N2}" -f $r.total_ils) -ForegroundColor Green
Show-State

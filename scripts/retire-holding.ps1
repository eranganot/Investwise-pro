# Retire a holding that no longer trades, moving its value to cash.
#
#   .\scripts\retire-holding.ps1 -Ticker COW
#   .\scripts\retire-holding.ps1 -Ticker COW -AmountIls 2109   # override the value
#   .\scripts\retire-holding.ps1 -Ticker COW -WhatIf           # show, change nothing
#
# For a delisted instrument: the quote still returns its last traded price
# forever, so the position sits in NAV at a frozen number. Redemption proceeds
# land as cash at the broker, and this makes the app's record say the same.
#
# NAV is unchanged by design: the position's value comes out, the identical
# amount goes into cash. `invested_ils` is untouched because it now reads the
# contributions ledger -- retiring a holding is not a deposit or a withdrawal.
#
# THIS IS BOOKKEEPING, NOT A TRADE. Nothing is bought or sold anywhere. Confirm
# with your broker what the position was actually redeemed for before trusting
# the number below: the app's figure is a stale quote, which is the whole
# problem.

param(
    [Parameter(Mandatory = $true)][string]$Ticker,
    [double]$AmountIls = 0,
    [switch]$WhatIf,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app"
)

$ErrorActionPreference = 'Continue'
# Fail fast rather than carry a hardcoded key. The agent key already sits in
# 11 committed smoke scripts and 162 commits of history (backlog #4 rotates
# them all in one change); this script is not going to become the twelfth.
#   $env:IW_AGENT_KEY = "<key>"    then re-run
if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor Gray
    exit 1
}
$H = @{ 'x-agent-key' = $env:IW_AGENT_KEY }
$Hj = $H + @{ 'Content-Type' = 'application/json' }

function Fail($m) { Write-Host $m -ForegroundColor Red; exit 1 }

# ---- 1. read the position, and say what the app currently believes ----------
try { $port = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio" -Headers $H -TimeoutSec 60 }
catch { Fail "Could not read the portfolio: $($_.Exception.Message)" }

$pos = @($port.positions | Where-Object { $_.ticker -eq $Ticker })[0]
if (-not $pos) { Fail "No holding called '$Ticker'. Nothing to do." }

$value = if ($AmountIls -gt 0) { $AmountIls } else { [double]$pos.value_ils }
$navBefore = [double]$port.nav_ils
$cashBefore = [double]$port.cash_ils

Write-Host ""
Write-Host "$Ticker" -ForegroundColor White
Write-Host ("  quantity     {0}" -f $pos.quantity) -ForegroundColor Gray
Write-Host ("  price        {0} {1}" -f $pos.current_price, $pos.currency) -ForegroundColor Gray
Write-Host ("  value        {0:N2} ILS" -f $pos.value_ils) -ForegroundColor Gray
Write-Host ("  moving       {0:N2} ILS to cash" -f $value) -ForegroundColor Cyan
Write-Host ("  NAV          {0:N2} -> {0:N2}  (unchanged)" -f $navBefore) -ForegroundColor DarkGray
Write-Host ("  cash         {0:N2} -> {1:N2}" -f $cashBefore, ($cashBefore + $value)) -ForegroundColor DarkGray
Write-Host ""

if ($WhatIf) { Write-Host "-WhatIf: nothing was changed." -ForegroundColor Yellow; exit 0 }
if ((Read-Host "Retire $Ticker and move $([math]::Round($value,2)) ILS to cash? (y/N)") -ne 'y') {
    Write-Host "Cancelled." -ForegroundColor Yellow; exit 0
}

# ---- 2. cash FIRST, then delete -------------------------------------------
# Order matters. Crediting first means a failure between the two steps leaves
# you over-counted, which is visible and reversible. Deleting first would lose
# the position and its value if the credit then failed.
$body = @{ amount_ils = $value; mode = 'adjust' } | ConvertTo-Json -Compress
try {
    $r = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/portfolio/cash" `
            -Headers $Hj -Body $body -TimeoutSec 60
    Write-Host ("Cash credited: {0:N2}" -f $r.cash_ils) -ForegroundColor Green
} catch { Fail "Could not credit cash: $($_.Exception.Message). Nothing was deleted." }

$url = "$BaseUrl/api/v1/portfolio/position?ticker=$([uri]::EscapeDataString($Ticker))"
try {
    Invoke-RestMethod -Method Delete -Uri $url -Headers $H -TimeoutSec 60 | Out-Null
    Write-Host "$Ticker removed." -ForegroundColor Green
} catch {
    Write-Host "Cash was credited but $Ticker could NOT be removed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Your book is now over-counted by $([math]::Round($value,2)). Remove $Ticker in Holdings, or re-run." -ForegroundColor Yellow
    exit 1
}

# ---- 3. prove it ----------------------------------------------------------
Start-Sleep -Seconds 2
try { $after = Invoke-RestMethod -Uri "$BaseUrl/api/v1/portfolio" -Headers $H -TimeoutSec 60 } catch { $after = $null }
if ($after) {
    $still = @($after.positions | Where-Object { $_.ticker -eq $Ticker }).Count
    Write-Host ""
    Write-Host ("NAV   {0:N2} -> {1:N2}   (drift {2:N2})" -f $navBefore, $after.nav_ils, ([double]$after.nav_ils - $navBefore)) -ForegroundColor Cyan
    Write-Host ("cash  {0:N2} -> {1:N2}" -f $cashBefore, $after.cash_ils) -ForegroundColor Cyan
    Write-Host ("invested {0:N2}  ({1})" -f $after.invested_ils, $after.invested_source) -ForegroundColor DarkGray
    if ($still -gt 0) { Write-Host "$Ticker is STILL present - check Holdings." -ForegroundColor Red }
    # A price moving elsewhere in the book between the two reads is normal;
    # anything near the retired value is not.
    if ([math]::Abs([double]$after.nav_ils - $navBefore) -gt ($value * 0.5)) {
        Write-Host "NAV moved by more than half the retired value - worth a look." -ForegroundColor Yellow
    }
}
Write-Host ""
Write-Host "Confirm at your broker what $Ticker was actually redeemed for." -ForegroundColor Cyan
Write-Host "If it differs, adjust cash:  .\scripts\set-contributions.ps1 -Show   then use Holdings." -ForegroundColor DarkGray

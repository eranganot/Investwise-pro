# Manage the strategy sleeves on your book from the command line.
#
#   .\scripts\set-sleeves.ps1                                    # show what runs
#   .\scripts\set-sleeves.ps1 -Add btm_factor_stack -Pct 15
#   .\scripts\set-sleeves.ps1 -Add btm_trend_soxl -Pct 20        # resize, not a second copy
#   .\scripts\set-sleeves.ps1 -Remove btm_trend_soxl
#   .\scripts\set-sleeves.ps1 -Remove btm_trend_soxl -WhatIf     # show, change nothing
#   .\scripts\set-sleeves.ps1 -FundPlan                          # C3 funding preview
#
# WHY THIS EXISTS. C2 makes "Apply strategy" additive: tapping it on a second
# card runs both sleeves. The Plan tab has no Remove control until C5, so
# without this you could add a sleeve from the phone and have no way to drop it.
# Same escape hatch retire-holding.ps1 and set-contributions.ps1 already are.
#
# NOTHING HERE PLACES A BROKERAGE ORDER, and -Remove does not sell anything. It
# stops the app steering toward that sleeve and puts its max_weight caps back;
# the shares stay exactly where they are.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Add = '',
    [double]$Pct = 0,
    [string]$Remove = '',
    [switch]$FundPlan,
    [switch]$WhatIf,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app"
)

$ErrorActionPreference = 'Continue'

# Fail fast rather than carry a hardcoded key (backlog #4 rotates the 11 that
# already exist; this is not becoming the twelfth).
if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor Gray
    exit 1
}
$H = @{ 'x-agent-key' = $env:IW_AGENT_KEY }

function Fail($m) { Write-Host $m -ForegroundColor Red; exit 1 }

function Api($method, $path, $tmo = 120) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "  $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

function Show-Sleeves($title) {
    $s = Api GET '/api/v1/plan/sleeves'
    if ($null -eq $s) { Fail "cannot read /api/v1/plan/sleeves" }
    Write-Host "`n$title" -ForegroundColor Cyan
    $rows = @($s.sleeves)
    if ($rows.Count -eq 0) {
        Write-Host "  (no sleeves - the whole book is objective-managed core)" -ForegroundColor DarkGray
    } else {
        foreach ($r in $rows) {
            Write-Host ("  {0,-24} {1,6:N1}%   added {2}" -f `
                $r.strategy_id, $r.sleeve_pct, $(if ($r.created_at) { $r.created_at.Substring(0,10) } else { '?' }))
        }
    }
    Write-Host ("  {0,-24} {1,6:N1}%   (the remainder, objective-managed)" -f 'core', $s.core_pct) -ForegroundColor DarkGray
    return $s
}

if ($FundPlan) {
    # Preview only - the API refuses to execute this path in C3a and says so.
    $r = Api POST '/api/v1/plan/sleeves/fund?dry_run=true' 180
    if ($null -eq $r) { Fail "cannot read the funding preview" }
    if (-not $r.ok) { Fail "$($r.error)" }
    Write-Host "`nFunding every under-funded sleeve, one shared budget" -ForegroundColor Cyan
    Write-Host ("  NAV {0:N0}   intended {1:N1}%   would end at {2:N1}%" -f `
        $r.nav, $r.intended_sleeve_pct, $r.resulting_sleeve_pct)
    Write-Host ""
    foreach ($s in @($r.sleeves)) {
        $colour = switch ($s.status) {
            'funded'       { 'Green' }
            'skipped'      { 'Red' }
            default        { 'DarkGray' }
        }
        Write-Host ("  {0,-24} {1,-14} {2,10:N0}" -f $s.strategy_id, $s.status, $s.amount_ils) -ForegroundColor $colour
        if ($s.reason) { Write-Host "      $($s.reason)" -ForegroundColor DarkGray }
    }
    if ($r.funding) {
        Write-Host "`n  $($r.funding_summary)" -ForegroundColor Yellow
        Write-Host ("  plan shortfall {0:N0} ({1:N2}% of NAV)" -f `
            $r.plan_shortfall_ils, ($r.plan_shortfall_ils / $r.nav * 100)) -ForegroundColor DarkGray
    }
    if ($r.message) { Write-Host "`n  $($r.message)" -ForegroundColor Yellow }
    Write-Host "`n  Preview only in this release. Nothing was sold or bought." -ForegroundColor DarkGray
    Write-Host "  Fund a single sleeve to execute today: Plan tab -> Fund this sleeve." -ForegroundColor DarkGray
    exit 0
}

if (-not $Add -and -not $Remove) {
    $null = Show-Sleeves "Sleeves on this book"
    Write-Host "`nTo change one:" -ForegroundColor DarkGray
    Write-Host "  .\scripts\set-sleeves.ps1 -Add <strategy_id> -Pct <n>" -ForegroundColor DarkGray
    Write-Host "  .\scripts\set-sleeves.ps1 -Remove <strategy_id>" -ForegroundColor DarkGray
    exit 0
}
if ($Add -and $Remove) { Fail "-Add and -Remove are separate operations. Run them one at a time." }

$before = Show-Sleeves "Before"

# ------------------------------------------------------------------------- add
if ($Add) {
    if ($Pct -le 0) { Fail "-Pct is required with -Add, and must be above zero. 0% is a removal, not a size." }
    $free = [double]$before.core_pct
    $own = @($before.sleeves | Where-Object { $_.strategy_id -eq $Add })
    # A resize frees its own current share first, which is what the API does too.
    if ($own.Count -gt 0) { $free += [double]$own[0].sleeve_pct }
    if ($Pct -gt $free + 0.05) {
        Fail ("That asks for {0:N1}% but only {1:N1}% of the book is free. Lower it, or shrink another sleeve first." -f $Pct, $free)
    }
    if ($WhatIf) {
        Write-Host ("`n-WhatIf: would set {0} to {1:N1}%. Nothing sent." -f $Add, $Pct) -ForegroundColor Yellow
        exit 0
    }
    $r = Api POST "/api/v1/strategies/$Add/apply?sleeve_pct=$Pct"
    if ($null -eq $r) { Fail "apply failed" }
    if (-not $r.ok) {
        Write-Host "`nRefused: $($r.error)" -ForegroundColor Red
        if ($r.reason) { Write-Host "  $($r.reason)" -ForegroundColor Yellow }
        exit 1
    }
    Write-Host "`nApplied: $($r.sleeve.action) $Add at $($r.sleeve.sleeve_pct)%" -ForegroundColor Green
    foreach ($c in @($r.sleeve_caps)) {
        Write-Host ("  cap {0,-6} {1,6:N1}%  ({2})" -f $c.ticker, $c.level, $c.action) -ForegroundColor Gray
    }
}

# ---------------------------------------------------------------------- remove
if ($Remove) {
    if (-not (@($before.sleeves) | Where-Object { $_.strategy_id -eq $Remove })) {
        Fail "'$Remove' is not a sleeve on this book. Nothing to remove."
    }
    if ($WhatIf) {
        Write-Host "`n-WhatIf: would remove $Remove and re-level its caps. Nothing sent." -ForegroundColor Yellow
        exit 0
    }
    $r = Api DELETE "/api/v1/plan/sleeves/$Remove"
    if ($null -eq $r) { Fail "remove failed" }
    if (-not $r.ok) { Fail "Refused: $($r.error)" }
    Write-Host "`nRemoved: $Remove (was $($r.was_pct)%)" -ForegroundColor Green
    $retired = @($r.retired_caps)
    if ($retired.Count -gt 0) {
        Write-Host "  caps retired (history kept, active=False): $($retired -join ', ')" -ForegroundColor Gray
    } else {
        Write-Host "  no caps retired - every ticker is still wanted by another sleeve" -ForegroundColor Gray
    }
    Write-Host "  Nothing was sold. Your shares are where they were." -ForegroundColor DarkGray
}

$null = Show-Sleeves "After"

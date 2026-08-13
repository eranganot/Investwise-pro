# Re-arm max_weight caps that the first C2 build silently retired.
#
#   .\scripts\rearm-caps.ps1                       # show what it would re-arm
#   .\scripts\rearm-caps.ps1 -Execute              # re-arm them
#   .\scripts\rearm-caps.ps1 -Only V,SCHD,MSFT -Execute
#
# WHY THIS EXISTS. The first C2 build's cap-retirement loop claimed ownership of
# every max_weight rule on the book, not just the ones the sleeve system armed.
# Applying or removing a sleeve therefore set active=False on hand-set caps that
# no sleeve ever wanted. Observed live: V 30%, SCHD 30%, MSFT 20% -- the last of
# which is the cap the 2026-08-11 notification fix is built around.
#
# The cause is fixed (caps are now MARKED as sleeve-owned and only marked ones
# are retired), but a fix does not un-retire what was already retired. This does.
#
# It only touches max_weight rules that are INACTIVE and were NOT armed by the
# sleeve system, so it cannot resurrect a cap you retired on purpose by removing
# a sleeve. Run it once and delete it.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string[]]$Only = @(),
    [switch]$Execute,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app"
)

$ErrorActionPreference = 'Continue'

if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = "<your agent key>"' -ForegroundColor Gray
    exit 1
}
$H = @{ 'x-agent-key' = $env:IW_AGENT_KEY }

function Api($method, $path, $tmo = 90) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $H -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "  $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

$all = Api GET '/api/v1/rules'
if ($null -eq $all) { Write-Host "cannot read /api/v1/rules" -ForegroundColor Red; exit 1 }

# The sleeve system marks what it owns in strategy_id. Anything inactive WITHOUT
# that mark is a cap it should never have touched.
$candidates = @(@($all.rules) | Where-Object {
    $_.rule_type -eq 'max_weight' -and -not $_.active -and $_.strategy_id -ne 'sleeve'
})
if ($Only.Count -gt 0) {
    $upper = @($Only | ForEach-Object { $_.ToUpper() })
    $candidates = @($candidates | Where-Object { $upper -contains $_.ticker.ToUpper() })
}

Write-Host "`nInactive max_weight caps not owned by the sleeve system" -ForegroundColor Cyan
if ($candidates.Count -eq 0) {
    Write-Host "  (none - nothing to re-arm)" -ForegroundColor Green
    Write-Host "`nIf you expected some, check whether they were retired for a different" -ForegroundColor DarkGray
    Write-Host "reason: list_rules retires a rule whose ticker you no longer hold." -ForegroundColor DarkGray
    exit 0
}
foreach ($r in $candidates) {
    Write-Host ("  {0,-6} {1,6:N1}%   {2}" -f $r.ticker, $r.level, $r.note) -ForegroundColor Gray
}

if (-not $Execute) {
    Write-Host "`nDry run. Re-run with -Execute to re-arm these $($candidates.Count)." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
$done = 0
foreach ($r in $candidates) {
    $res = Api POST "/api/v1/rules/$($r.id)/toggle"
    if ($null -ne $res -and $res.ok) {
        Write-Host ("  re-armed {0} at {1:N1}%" -f $r.ticker, $r.level) -ForegroundColor Green
        $done++
    } else {
        Write-Host ("  FAILED to re-arm {0}" -f $r.ticker) -ForegroundColor Red
    }
}

# Verify by reading back, rather than trusting the toggle responses. A toggle is
# a FLIP, so a stale view of `active` would turn a cap off instead of on -- read
# the truth before saying it worked.
$after = Api GET '/api/v1/rules'
if ($null -ne $after) {
    $still = @(@($after.rules) | Where-Object {
        $_.rule_type -eq 'max_weight' -and -not $_.active -and $_.strategy_id -ne 'sleeve'
    })
    if ($Only.Count -gt 0) {
        $upper = @($Only | ForEach-Object { $_.ToUpper() })
        $still = @($still | Where-Object { $upper -contains $_.ticker.ToUpper() })
    }
    if ($still.Count -eq 0) { Write-Host "`nVerified: $done re-armed, none left inactive." -ForegroundColor Green }
    else { Write-Host "`nStill inactive: $(($still | ForEach-Object { $_.ticker }) -join ', ')" -ForegroundColor Red }
}

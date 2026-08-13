# SMOKE - C3: funding N sleeves against one budget.
#
#   .\scripts\smoke\smoke-c3.ps1                  # read-only, safe
#   .\scripts\smoke\smoke-c3.ps1 -SkipShaCheck
#   .\scripts\smoke\smoke-c3.ps1 -AllowExecute    # REALLY FUNDS. Sells shares.
#
# ==> READ THIS BEFORE ADDING A FLAG <==
#
# C3b turned multi-sleeve execution ON. `dry_run=false` is now a REAL TRADE: it
# sells holdings and buys sleeve legs. The C3a version of this script called it
# unconditionally to prove execution was refused, which was safe only while the
# gate was shut. On the first run after the flip it fired a live write at
# production and got away with it purely because every sleeve was already at
# target, so the plan was empty. With one under-funded sleeve it would have sold
# VXUS and SCHD unasked.
#
# So: nothing here executes unless you pass -AllowExecute. The default run
# proves the thing that actually matters in production -- that a dry run moves
# nothing -- and prints the plan.
#
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param(
    [string]$Sha = '',
    [switch]$SkipShaCheck,
    [switch]$AllowExecute,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'

if (-not $env:IW_AGENT_KEY) {
    Write-Host "IW_AGENT_KEY is not set. Set it and re-run:" -ForegroundColor Red
    Write-Host '  $env:IW_AGENT_KEY = [regex]::Match((Get-Content .\scripts\smoke\smoke-p3.ps1 -Raw), ''iwk_[A-Za-z0-9_\-]+'').Value' -ForegroundColor Gray
    exit 1
}
# PowerShell variables are CASE-INSENSITIVE: $H and $h are one variable. Naming
# the header hashtable $H and then doing `$h = Api GET '/health'` silently
# replaces the headers with the health response, and every later call dies with
# "Cannot bind parameter 'Headers'". Hence $ApiHeaders, matching smoke-c1/c2.
$ApiHeaders = @{ 'x-agent-key' = $env:IW_AGENT_KEY }

$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $tmo = 180) {
    try {
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host "Smoke: C3 whole-plan funding  ($BaseUrl)" -ForegroundColor White

# =========================================================================== #
Sec "C3.0  am I talking to the new container?"
# =========================================================================== #
if ($SkipShaCheck) { Skip "SHA check skipped" }
else {
    if (-not $Sha) { try { $Sha = (git rev-parse --short HEAD).Trim() } catch { $Sha = '' } }
    $h = Api GET '/health' 60
    if ($null -eq $h) { Bad "/health unreachable - stopping"; exit 1 }
    $live = "$($h.commit)"
    if (-not $live -or $live -eq 'unknown') { Bad "/health reports no commit" }
    elseif (-not $Sha) { Skip "no local SHA to compare; deployed is $live" }
    elseif ($live.StartsWith($Sha) -or $Sha.StartsWith($live)) { Ok "deployed commit is $live" }
    else { Bad "deployed is $live, you are smoking $Sha"; exit 1 }
}

# =========================================================================== #
Sec "C3.1  the route exists and previews"
# =========================================================================== #
$r = Api POST '/api/v1/plan/sleeves/fund?dry_run=true'
if ($null -eq $r) {
    Bad "POST /api/v1/plan/sleeves/fund did not respond - C3 is not deployed"
    exit 1
}
if (-not $r.ok) {
    if ("$($r.error)" -match 'no sleeves') {
        Skip "no sleeves on this book - apply one, then re-run"
        exit 0
    }
    Bad "preview refused: $($r.error)"
    exit 1
}
Ok "the whole-plan funding preview responds"
if ($r.dry_run -eq $true) { Ok "and says it is a dry run" } else { Bad "dry_run is not true on a preview" }

# =========================================================================== #
Sec "C3.2  what it would actually do  -- READ THIS"
# =========================================================================== #
Write-Host ("        NAV {0:N0}    intended {1:N1}%    would end at {2:N1}%" -f `
    $r.nav, $r.intended_sleeve_pct, $r.resulting_sleeve_pct) -ForegroundColor Gray
Write-Host ""
foreach ($s in @($r.sleeves)) {
    $c = switch ($s.status) { 'funded' { 'Green' } 'skipped' { 'Red' } default { 'DarkGray' } }
    Write-Host ("        {0,-24} {1,-14} {2,10:N0}" -f $s.strategy_id, $s.status, $s.amount_ils) -ForegroundColor $c
    if ($s.reason) { Write-Host "            $($s.reason)" -ForegroundColor DarkGray }
    foreach ($b in @($s.buys)) {
        Write-Host ("            buy {0,-6} {1,10:N0}" -f $b.ticker, $b.buy_ils) -ForegroundColor DarkGray
    }
}
if ($r.funding) {
    Write-Host "`n        FUNDED BY:" -ForegroundColor Yellow
    Write-Host ("        cash {0:N0} above the {1:P0} floor" -f $r.funding.from_cash_ils, $r.funding.cash_floor_pct) -ForegroundColor Gray
    foreach ($sell in @($r.funding.sells)) {
        Write-Host ("        sell {0,-6} {1,4} shares  {2,10:N0}   tax {3,8:N0}   {4}" -f `
            $sell.ticker, $sell.shares, $sell.value_ils, $sell.tax_ils, $sell.reason) -ForegroundColor Gray
    }
    Write-Host ("        estimated tax total {0:N0}" -f $r.funding.tax_ils) -ForegroundColor Yellow
}

# =========================================================================== #
Sec "C3.3  no sleeve is a funding source for another"
# =========================================================================== #
# A check that cannot fail is not a check. The first version did
# `foreach ($x in @($r.funding.sells))` with $r.funding null -- @($null) is a
# one-element array containing $null, so $x.ticker threw, $sells stayed empty,
# and the comparison then "passed" having compared nothing. Exactly the shape of
# the C2 smoke reporting "no cap left behind" while three caps were disarmed.
$sells = @()
if ($r.funding -and $r.funding.sells) {
    foreach ($x in @($r.funding.sells)) { if ($x) { $sells += "$($x.ticker)".ToUpper() } }
}
if ($sells.Count -eq 0) {
    Skip "no funding sales in this plan - there is nothing here to check"
    Write-Host "        Not a pass: it means the preview proposed no trims at all." -ForegroundColor DarkGray
} else {
    $sleeveTickers = @()
    foreach ($s in @($r.sleeves)) {
        foreach ($b in @($s.buys)) { if ($b) { $sleeveTickers += "$($b.ticker)".ToUpper() } }
    }
    $bad = @($sells | Where-Object { $sleeveTickers -contains $_ })
    if ($bad.Count -eq 0) {
        Ok "$($sells.Count) funding sale(s), none of them a ticker a sleeve wants"
    } else {
        Bad "funding proposes selling sleeve tickers: $($bad -join ', ')"
    }
}

# =========================================================================== #
Sec "C3.4  the arithmetic and the honesty of the result"
# =========================================================================== #
$sumFunded = 0
foreach ($s in @($r.sleeves)) { if ($s.status -eq 'funded') { $sumFunded += [double]$s.amount_ils } }
if ([Math]::Abs($sumFunded - [double]$r.amount_ils) -lt 1) {
    Ok ("the plan totals the funded sleeves ({0:N0})" -f $r.amount_ils)
} else {
    Bad "sleeves sum to $sumFunded but the plan says $($r.amount_ils)"
}

$shortPct = if ($r.nav) { [double]$r.plan_shortfall_ils / [double]$r.nav * 100 } else { 0 }
if ($shortPct -lt 1.0) {
    Ok ("residual {0:N0} is {1:N2}% of NAV, inside the one-point tolerance" -f $r.plan_shortfall_ils, $shortPct)
} else {
    Bad ("residual {0:N0} is {1:N2}% of NAV - outside the tolerance this claims" -f $r.plan_shortfall_ils, $shortPct)
}

if ($r.fully_funded) {
    Ok "every sleeve fits - no partial result to explain"
} else {
    Skip "NOT fully funded - and it says so rather than reporting success"
    Write-Host "        $($r.message)" -ForegroundColor Yellow
}

# =========================================================================== #
Sec "C3.5  a dry run moves nothing"
# =========================================================================== #
# The property worth checking against a live book now that the gate is open.
# Execution being ON is covered by the suite; it cannot be probed here without
# actually trading, which is what -AllowExecute is for.
function Snapshot() {
    $p = Api GET '/api/v1/portfolio' 60
    if ($null -eq $p) { return $null }
    return (($p.positions | ForEach-Object { "$($_.ticker):$($_.quantity)" }) -join ',')
}

$before = Snapshot
for ($i = 0; $i -lt 2; $i++) { $null = Api POST '/api/v1/plan/sleeves/fund?dry_run=true' }
$after = Snapshot
if ($null -eq $before -or $null -eq $after) { Skip "cannot read the portfolio to compare" }
elseif ($after -eq $before) { Ok "three previews later, holdings are identical" }
else { Bad "A DRY RUN MOVED HOLDINGS. Stop and investigate before funding anything." }

# =========================================================================== #
if ($AllowExecute) {
Sec "C3.6  EXECUTING FOR REAL  -- this sells shares"
# =========================================================================== #
    if (-not $r.funding -or -not @($r.funding.sells)) {
        Skip "nothing to fund, so there is nothing to execute"
    } else {
        Write-Host "        About to sell:" -ForegroundColor Red
        foreach ($sell in @($r.funding.sells)) {
            Write-Host ("          {0} {1} shares for {2:N0}, tax {3:N0}" -f `
                $sell.ticker, $sell.shares, $sell.value_ils, $sell.tax_ils) -ForegroundColor Red
        }
        Write-Host "        Ctrl-C now if that is not what you want." -ForegroundColor Yellow
        Start-Sleep -Seconds 6

        $x = Api POST '/api/v1/plan/sleeves/fund?dry_run=false' 300
        if ($null -eq $x) { Bad "the execute call did not respond - CHECK YOUR BOOK BY HAND" }
        elseif (-not $x.ok) { Bad "execution refused: $($x.error) - $($x.reason)" }
        else {
            Ok "executed. leg scale $($x.leg_scale)"
            foreach ($b in @($x.bought)) {
                Write-Host ("          bought {0,-6} {1,10:N0}" -f $b.ticker, $b.amount_ils) -ForegroundColor Green
            }
            foreach ($s in @($x.sold)) {
                Write-Host ("          sold   {0,-6} {1,10:N0}  tax {2:N0}" -f $s.ticker, $s.value_ils, $s.tax_ils) -ForegroundColor Green
            }
            if (@($x.skipped)) { Bad "legs were SKIPPED, so a sleeve is short: $(@($x.skipped) | ConvertTo-Json -Compress)" }
            else { Ok "no leg was dropped" }
        }
    }
} else {
Sec "C3.6  real execution - not attempted"
    Skip "pass -AllowExecute to actually fund. It sells shares and realises tax."
}

# =========================================================================== #
Write-Host "`n----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "C3 SMOKE: $pass passed, $fail failed, $skip skipped" -ForegroundColor $(if ($fail) { 'Red' } else { 'Green' })
if ($skip -gt 0) { Write-Host "A SKIP is not a pass - read the note under each one." -ForegroundColor Yellow }

# The point of C3a is to READ a real funding plan before enabling the write. If
# there was nothing to fund, this run proved the endpoint works and told you
# nothing whatsoever about the money path. Say so, loudly, rather than letting
# "10 passed" read as "the funding logic is verified".
if ($sells.Count -eq 0) {
    Write-Host "`n  THIS RUN DID NOT EXERCISE THE MONEY PATH." -ForegroundColor Yellow
    Write-Host "  Every sleeve was already at its target, so there were no trims and no" -ForegroundColor Yellow
    Write-Host "  tax to check. Green here is NOT evidence that C3b is safe to enable." -ForegroundColor Yellow
    Write-Host "  To get a real plan to read, add a second sleeve and re-run:" -ForegroundColor Gray
    Write-Host "    .\scripts\set-sleeves.ps1 -Add btm_factor_stack -Pct 15" -ForegroundColor Gray
    Write-Host "    .\scripts\smoke\smoke-c3.ps1" -ForegroundColor Gray
    Write-Host "    .\scripts\set-sleeves.ps1 -Remove btm_factor_stack" -ForegroundColor Gray
    Write-Host "  All read-only except the sleeve row and its caps, which C2 round-trips." -ForegroundColor DarkGray
} else {
    Write-Host "The trims and the tax above are what -AllowExecute would actually do." -ForegroundColor Yellow
}
if ($fail) { exit 1 }

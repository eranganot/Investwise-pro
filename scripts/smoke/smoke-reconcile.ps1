# SMOKE - PR 3: the trade ledger, and the repair it makes recordable.
#
#   .\scripts\smoke\smoke-reconcile.ps1 -Sha <sha>            # READ-ONLY
#   .\scripts\smoke\smoke-reconcile.ps1 -Scale 1.234          # dry run, still read-only
#   .\scripts\smoke\smoke-reconcile.ps1 -Scale 1.234 -Apply   # WRITES. Rewrites every holding.
#
# READ-ONLY by default, and -Scale alone is STILL read-only: the API refuses to
# write without confirm=true, which only -Apply sends.
#
# WHAT THIS IS FOR. `transactions` existed from the first schema and had zero
# writers for the life of the app, so nav_history had no flows and a book change
# read as market performance (+34.62% on a book that gained 4%). And when an FX
# bug minted 7,388 of positions on 2026-08-21 there was nothing to reconstruct
# it from.
#
# THE REPAIR IS ONE NUMBER, and that is not luck: the bug multiplied every
# holding in a class by the SAME factor and never touched cost_basis per share.
# The book has the right shape and the wrong size, so true_qty = qty / s.
#
# R2 SHOWS THE ANCHORS AND DELIBERATELY DOES NOT CHOOSE. They disagree, and the
# disagreement is real - the deposits anchor over-corrects because a real sale
# re-stamped basis at market. Read both, then pass -Scale yourself.
#
# House rules: a SKIP is never a PASS. Print the exception TYPE on first
# failure. Headers are $ApiHeaders, never $h.

param(
    [string]$Sha = "",
    [switch]$SkipShaCheck,
    [double]$Scale = 0,
    [switch]$Apply,
    [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
$pass = 0; $fail = 0; $skip = 0
[Net.ServicePointManager]::DefaultConnectionLimit = 100
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host "`n$m" -ForegroundColor Cyan }

function Api($method, $path, $body = $null, $tmo = 90) {
    try {
        if ($null -ne $body) {
            $json = ($body | ConvertTo-Json -Depth 6)
            return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -ContentType 'application/json' -Body $json -TimeoutSec $tmo
        }
        return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo
    } catch {
        $code = $null
        try { $code = $_.Exception.Response.StatusCode.value__ } catch {}
        Write-Host "        $method $path -> $($_.Exception.GetType().Name): $(if($code){"HTTP $code - "})$($_.Exception.Message)" -ForegroundColor DarkRed
        return $null
    }
}

Write-Host ""
Write-Host "SMOKE - ledger and reconciliation (PR 3)" -ForegroundColor White
Write-Host "  $BaseUrl" -ForegroundColor DarkGray
if ($Apply) { Write-Host "  -Apply IS SET. R4 will REWRITE EVERY HOLDING on your book." -ForegroundColor Yellow }

Sec "R0. The build under test is the one serving"
$health = Api GET "/health"
if ($null -eq $health) { Bad "R0.1 /health did not answer - STOP" }
else {
    Ok "R0.1 serving $($health.commit)"
    if ($SkipShaCheck) { Skip "R0.2 -SkipShaCheck set" }
    elseif (-not $Sha) { Skip "R0.2 no -Sha given" }
    elseif ($health.commit -eq $Sha.Substring(0, [Math]::Min(7, $Sha.Length))) { Ok "R0.2 serving the commit under test" }
    else { Bad "R0.2 serving $($health.commit), expected $Sha - STOP" }
}

Sec "R1. The book, before anything"
$pf = Api GET "/api/v1/portfolio"
if ($null -eq $pf) { Bad "R1.0 /api/v1/portfolio did not answer" }
else {
    Write-Host ("        NAV {0:N2} | basis {1:N2} | deposits {2:N2}" -f $pf.nav_ils, $pf.cost_basis_ils, $pf.contributed_ils) -ForegroundColor DarkGray
    foreach ($p in $pf.positions) {
        Write-Host ("          {0,-6} {1,12:N6} sh  {2,12:N2}" -f $p.ticker, $p.quantity, $p.value_ils) -ForegroundColor DarkGray
    }
    if ($null -ne $pf.unexplained_basis_ils -and [Math]::Abs([double]$pf.unexplained_basis_ils) -ge 1.0) {
        Skip ("R1.1 {0:N2} of cost basis has no deposit behind it - this is what R2 exists to remove" -f $pf.unexplained_basis_ils)
    } else {
        Ok "R1.1 cost basis and deposits agree - nothing to reconcile"
    }
}

Sec "R2. The anchors, and their arithmetic"
$pv = Api GET "/api/v1/portfolio/reconcile/preview"
if ($null -eq $pv) {
    Bad "R2.0 preview did not answer - the deploy may predate PR 3"
} elseif ($pv.ok -ne $true) {
    Bad "R2.0 preview refused: $($pv.error)"
} else {
    Ok "R2.1 preview answered (read-only, version $($pv.version))"
    if ($pv.auto_apply_forbidden -eq $true) { Ok "R2.2 the payload forbids auto-apply" }
    else { Bad "R2.2 a ranked estimate must never be auto-appliable" }

    if (-not $pv.anchors -or $pv.anchors.Count -eq 0) {
        Skip "R2.3 no anchor could be computed - record a deposit or a NAV snapshot first"
    } else {
        foreach ($a in $pv.anchors) {
            Write-Host ""
            Write-Host ("        anchor '{0}'  s = {1}" -f $a.id, $a.scale) -ForegroundColor White
            Write-Host ("          from: {0}" -f $a.basis) -ForegroundColor DarkGray
            Write-Host ("          would give: NAV {0:N2}, basis {1:N2}" -f $a.resulting_nav_ils, $a.resulting_basis_ils) -ForegroundColor DarkGray
            Write-Host ("          caveat: {0}" -f $a.caveat) -ForegroundColor DarkYellow
        }
        Write-Host ""
        Ok ("R2.3 {0} anchor(s), each with its arithmetic and its caveat" -f $pv.anchors.Count)
        $scales = @($pv.anchors | ForEach-Object { [double]$_.scale })
        if ($scales.Count -gt 1) {
            $lo = ($scales | Measure-Object -Minimum).Minimum
            $hi = ($scales | Measure-Object -Maximum).Maximum
            Write-Host ("        the honest answer is bracketed: {0:N6} <= s <= {1:N6}" -f $lo, $hi) -ForegroundColor Cyan
        }
    }
}

Sec "R3. Apply refuses without confirm"
if ($Scale -le 0) {
    Skip "R3.1 no -Scale given, nothing to dry-run"
} else {
    $dry = Api POST "/api/v1/portfolio/reconcile/apply" @{ scale = $Scale }
    if ($null -eq $dry) {
        Bad "R3.1 dry run did not answer"
    } elseif ($dry.ok -eq $false -and $dry.would_scale_by) {
        Ok ("R3.1 dry run: would scale by {0}, wrote nothing" -f $dry.would_scale_by)
        $after = Api GET "/api/v1/portfolio"
        if ($null -ne $after -and [Math]::Abs([double]$after.nav_ils - [double]$pf.nav_ils) -lt 0.01) {
            Ok "R3.2 the book is unchanged after the dry run"
        } else {
            Bad "R3.2 an unconfirmed apply CHANGED THE BOOK"
        }
    } else {
        Bad "R3.1 apply did not refuse an unconfirmed write: $($dry | ConvertTo-Json -Compress)"
    }
}

Sec "R4. Apply (WRITES - opt in)"
if (-not $Apply) {
    Skip "R4.1 not run. Pass -Scale <s> -Apply once you have chosen a scale from R2."
} elseif ($Scale -le 0) {
    Bad "R4.1 -Apply needs a -Scale"
} else {
    Write-Host "        rewriting every holding by 1/$Scale ..." -ForegroundColor Yellow
    $res = Api POST "/api/v1/portfolio/reconcile/apply" @{ scale = $Scale; confirm = $true; note = "smoke s=$Scale" }
    if ($null -eq $res -or $res.ok -ne $true) {
        Bad "R4.1 apply failed: $($res.error)"
    } else {
        Ok ("R4.1 applied. removed {0:N2} of phantom value" -f $res.removed_ils)
        foreach ($a in $res.adjusted) {
            Write-Host ("          {0,-6} {1,12:N6} -> {2,12:N6}" -f $a.ticker, $a.before, $a.after) -ForegroundColor DarkGray
        }
        $now = Api GET "/api/v1/portfolio"
        if ($null -ne $now) {
            Write-Host ("        NAV {0:N2} | basis {1:N2} | gap {2:N2}" -f $now.nav_ils, $now.cost_basis_ils, $now.unexplained_basis_ils) -ForegroundColor DarkGray
            if ($null -ne $now.unexplained_basis_ils -and [Math]::Abs([double]$now.unexplained_basis_ils) -lt [Math]::Abs([double]$pf.unexplained_basis_ils)) {
                Ok "R4.2 the unexplained gap shrank"
            } else {
                Bad "R4.2 the gap did not shrink - check the scale you chose"
            }
        }
    }
}

Write-Host ""
Write-Host "  PASS $pass   FAIL $fail   SKIP $skip" -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ""
Write-Host "  R2 does not choose for you, on purpose. Read both anchors, then:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke\smoke-reconcile.ps1 -Sha $Sha -Scale <s>          # dry run" -ForegroundColor Gray
Write-Host "    .\scripts\smoke\smoke-reconcile.ps1 -Sha $Sha -Scale <s> -Apply   # writes" -ForegroundColor Gray
Write-Host ""
if ($fail -gt 0) { exit 1 }
exit 0

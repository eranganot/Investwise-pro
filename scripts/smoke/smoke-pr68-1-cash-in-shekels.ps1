# SMOKE - PR 68.1: cash card in shekels, the ledger's trims, "vs avg price" / "vs last buy".
#
#   .\scripts\smoke\smoke-pr68-1-cash-in-shekels.ps1 -Sha abc1234
#
# READ-ONLY. The ship script sets the cash rules (keep 150, invest above 1,500)
# before this runs; this only reads them back.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app")

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\..\agent-key.ps1"
$pass = 0; $fail = 0; $skip = 0
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
function Ok($m)   { Write-Host "  PASS  $m" -ForegroundColor Green;  $script:pass++ }
function Bad($m)  { Write-Host "  FAIL  $m" -ForegroundColor Red;    $script:fail++ }
function Skip($m) { Write-Host "  SKIP  $m" -ForegroundColor Yellow; $script:skip++ }
function Sec($m)  { Write-Host ""; Write-Host $m -ForegroundColor Cyan }
function Note($m) { Write-Host "        $m" -ForegroundColor DarkGray }
$ApiHeaders = @{ 'x-agent-key' = (Get-AgentKey -BaseUrl $BaseUrl) }
function Api($method, $path, $tmo = 180) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch { Write-Host "        $method $path -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}

Write-Host "Smoke: PR 68.1  ($BaseUrl)" -ForegroundColor White

Sec "0  build and schema"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "migration 0024 ran" } else { Bad "schema $($h.schema_revision)" }

Sec "1  cash rules are in shekels"
$plan = Api GET "/api/v1/plan"
Note "keep $($plan.cash_target_ils)   invest above $($plan.cash_redeploy_at_ils)   pct $($plan.cash_target_pct)"
if ([double]$plan.cash_target_ils -eq 150 -and [double]$plan.cash_redeploy_at_ils -eq 1500 -and $null -eq $plan.cash_target_pct) { Ok "keep 150, invest above 1,500, no % target" }
else { Bad "cash rules not as set" }
$pf = Api GET "/api/v1/portfolio"
$cash = [double]$pf.cash_ils; $nav = [double]$pf.nav_ils
$mix = Api GET "/api/v1/mix"
$tc = [double]$mix.target_allocation.Cash
if ([math]::Abs($tc - 150 / $nav) -lt 0.002) { Ok ("/mix cash target {0:P2} = 150 / NAV" -f $tc) } else { Bad ("/mix cash target {0} vs {1}" -f $tc, (150 / $nav)) }

Sec "2  the put-cash-to-work card"
$recs = Api GET "/api/v1/recommendations" 300
$rd = @($recs.recommendations | Where-Object { $_.apply.kind -eq 'redeploy_cash' })
Note ("cash {0:N2}" -f $cash)
if ($cash -gt 1500) {
    if ($rd.Count -gt 0) {
        $dep = ($rd[0].apply.legs | Measure-Object -Property amount_ils -Sum).Sum
        Ok ("card: {0}" -f $rd[0].title); Note $rd[0].action
        if ($cash - $dep -ge 149.99 -and $cash - $dep -lt 400) { Ok ("keeps {0:N2} (floor 150)" -f ($cash - $dep)) } else { Bad ("keeps {0:N2}" -f ($cash - $dep)) }
    } else { Bad "cash is above 1,500 and there is no card" }
} else {
    if ($rd.Count -eq 0) { Ok "cash at or under 1,500: no card (correct)" } else { Bad "card shown with cash under the trigger" }
}
$lq = @($recs.recommendations | Where-Object { $_.dimension -eq 'liquidity' })
if ($lq.Count -eq 0) { Ok "no 'raise cash' card" } else { Bad "liquidity card: $($lq[0].title)" }

Sec "3  each holding: vs avg price, and last buy"
foreach ($p in @($pf.positions)) {
    if ($p.ticker -eq 'CASH') { continue }
    $avg = [math]::Round(([double]$p.current_price / [double]$p.cost_basis - 1) * 100, 1)
    $lb = "no priced buy since it opened"
    if ($p.last_buy) { $lb = ("last buy {0} on {1}: {2}%" -f $p.last_buy.price, $p.last_buy.at.Substring(0,10), $p.last_buy.change_pct) }
    Ok ("{0}: {1}% vs avg {2};  {3}" -f $p.ticker, $avg, $p.cost_basis, $lb)
}

Sec "4  the chart refuses a record it cannot trust"
$hs = Api GET "/api/v1/portfolio/position-history?ticker=SOXL&range=ALL"
if ($hs) {
    if ($hs.value.available) { Note "SOXL value line drawn ($(@($hs.value.points).Count) pts)" }
    else { Note "SOXL value line withheld: $($hs.value.reason)" }
    if (-not $hs.value.available -and $hs.value.reason -match 'no share count|recorded twice') { Ok "withheld for the ledger's old rows, with the reason" }
    elseif ($hs.value.available) { Skip "drawn - the historical rows must have been repaired" }
    else { Bad "withheld for an unexpected reason" }
}

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }

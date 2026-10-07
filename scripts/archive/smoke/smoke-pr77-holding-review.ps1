# SMOKE - PR 77: the holding review; a sell card only with a measured edge.
#
#   .\scripts\smoke\smoke-pr77-holding-review.ps1 -Sha abc1234
#
# READ-ONLY. Accepts nothing. (The ship script measures first, with
# POST /holding-review/refresh - a measurement, not a change to the book.)
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
function Api($method, $path, $tmo = 300) {
    try { return Invoke-RestMethod -Method $method -Uri "$BaseUrl$path" -Headers $ApiHeaders -TimeoutSec $tmo }
    catch { Write-Host "        $method $path -> $($_.Exception.Message)" -ForegroundColor DarkRed; return $null }
}

Write-Host "Smoke: PR 77  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 77 has no migration" } else { Bad "schema $($h.schema_revision)" }

Sec "1  one review row per holding"
$recs = Api GET "/api/v1/recommendations"
if ($null -eq $recs) { Bad "/recommendations did not answer"; exit 1 }
$rows = @($recs.holding_review)
if (-not ($recs.PSObject.Properties.Name -contains 'holding_review')) { Bad "no holding_review block (old code)" }
else {
    $pf = Api GET "/api/v1/portfolio"
    $held = @(@($pf.positions) | Where-Object { $_ -and ([string]$_.ticker).ToUpper() -ne 'CASH' -and $_.asset_class -ne 'Cash' } | ForEach-Object { ([string]$_.ticker).ToUpper() } | Sort-Object -Unique)
    $got = @($rows | ForEach-Object { $_.ticker } | Sort-Object -Unique)
    foreach ($r in $rows) {
        $mm = if ($r.sleeve) { $r.sleeve } elseif ($r.measurement_usable) { "short edge " + $r.measurement.short.edge + ", long edge " + $r.measurement.long.edge } else { $r.measurement_note }
        Note ("{0,-6} {1,-10} {2}" -f $r.ticker, $r.state, $mm)
    }
    if ($held.Count -gt 0 -and (@(Compare-Object $held $got).Count -eq 0)) { Ok ("{0} holdings, {0} rows" -f $held.Count) }
    elseif ($held.Count -eq 0) { Skip "could not read the holdings list to compare" }
    else { Bad ("holdings {0} vs rows {1}" -f ($held -join ','), ($got -join ',')) }
    $unmeasured = @($rows | Where-Object { -not $_.sleeve -and -not $_.measurement_usable })
    if ($unmeasured.Count -eq 0) { Ok "every non-sleeve holding has a fresh measurement" }
    else { Skip ("{0} holding(s) not measured yet: {1}" -f $unmeasured.Count, (($unmeasured | ForEach-Object { $_.ticker }) -join ', ')) }
}

Sec "2  every review card acts; a sell only with a measured edge"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$rc = @($all | Where-Object { $_.dimension -eq 'holding_review' })
if ($rc.Count -eq 0) { Skip "no review card right now (no holding in Trim/Exit)" }
foreach ($c in $rc) {
    Note $c.title
    $isSell = $c.apply.kind -in @('trim', 'sell_position')
    if ($isSell -and -not ($c.meta.measurement.measured -and $c.meta.measurement.edge)) { Bad ("actionable sell without a measured edge: {0}" -f $c.title) }
    elseif ($isSell) { Ok ("{0}: rule {1}%/yr vs hold {2}%/yr" -f $c.meta.ticker, $c.meta.measurement.rule_cagr_pct, $c.meta.measurement.hold_cagr_pct) }
    elseif ($c.apply.kind -eq 'create_rules' -and @($c.apply.rules).Count -eq 1 -and [double]@($c.apply.rules)[0].level -gt 0) { Ok ("{0}: no edge, arms a stop at {1}" -f $c.meta.ticker, @($c.apply.rules)[0].level) }
    else { Bad ("{0}: card without an action ({1})" -f $c.meta.ticker, $c.apply.kind) }
}

Sec "3  the page"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Skip "could not fetch the page" }
elseif ($page -match 'function reviewTable') { Ok "Today shows the holding review" } else { Bad "old page" }

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }

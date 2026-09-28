# SMOKE - PR 73: a rebalance is trades; the history repair is ready; the chart is honest.
#
#   .\scripts\smoke\smoke-pr73-a-rebalance-is-trades.ps1 -Sha abc1234
#
# READ-ONLY. It does not accept the rebalance card and does not apply the repair
# (that is scripts\ops\repair-ledger-pr73.ps1, preview first, your "y").
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

Write-Host "Smoke: PR 73  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 73 has no migration" } else { Bad "schema $($h.schema_revision)" }

Sec "1  the history repair can see every unrecorded rebalance (preview only)"
$p = Api GET "/api/v1/portfolio/ledger/repair"
$ur = $null; if ($p) { $ur = $p.unrecorded_rebalances }
if ($null -eq $ur) { Bad "no unrecorded_rebalances section" }
else {
    Note ("{0} unrecorded rebalance accepts" -f @($ur.accepts).Count)
    $tks = @($ur.adjusts | ForEach-Object { $_.ticker } | Sort-Object -Unique)
    Note ("would adjust: {0}" -f ($tks -join ', '))
    foreach ($t in @('USO', 'UGA', 'VTI', 'QQQ')) {
        if ($tks -contains $t) { Ok "$t is reconstructed" }
        else { Bad "$t not reconstructed (measured 2026-09-28: it should be)" }
    }
    if (@($ur.disagreements).Count -eq 0) { Ok "no factor disagreements" } else { Bad ("{0} disagreements" -f @($ur.disagreements).Count) }
    foreach ($a in @($ur.adjusts | Where-Object { $_.ticker -in @('USO', 'UGA') })) {
        if ([math]::Abs([double]$a.factor - 1.0295) -lt 0.0005) { Ok ("{0} factor {1} (Commodities, 09-26)" -f $a.ticker, $a.factor) } else { Bad ("{0} factor {1}" -f $a.ticker, $a.factor) }
    }
}

Sec "2  a rebalance card quotes its tax"
$recs = Api GET "/api/v1/recommendations"
$rb = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ -and $_.apply.kind -eq 'rebalance_to_objective' })
if ($rb.Count -eq 0) { Skip "no rebalance card on Today right now" }
else {
    foreach ($c in $rb) {
        $line = @($c.how) | Where-Object { $_ -match 'real trades' }
        if ($line) { Ok ("{0}: {1}" -f $c.title, $line) } else { Bad ("{0}: no trades/tax line" -f $c.title) }
        if ($null -ne $c.rebalance_tax_ils) { Ok ("estimated tax on the card: {0}" -f $c.rebalance_tax_ils) } else { Bad "rebalance_tax_ils missing" }
    }
}

Sec "3  the position chart does not show the price as your position"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Skip "could not fetch the page" }
elseif ($page -match "Can't draw your position yet" -and $page -match 'price \(not your position\)') { Ok "served page says so" }
else { Bad "served page still shows the price under Your position" }
$q = Api GET "/api/v1/portfolio/position-history?ticker=QQQ&range=1M"
if ($q) {
    if ($q.value.available) { Ok "QQQ position line drawn (the repair has been applied)" }
    else { Note ("QQQ: {0}" -f $q.value.reason); Skip "QQQ waits for the repair script" }
}

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }

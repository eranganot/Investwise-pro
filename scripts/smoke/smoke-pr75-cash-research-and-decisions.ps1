# SMOKE - PR 75: cash above the trigger is researched; a take-profit sale keeps the
# sleeve target and holds the buy-back; the decision card recommends; the popup fits.
#
#   .\scripts\smoke\smoke-pr75-cash-research-and-decisions.ps1 -Sha abc1234
#
# READ-ONLY. It accepts nothing. The cash check uses ?simulate_cash=, a preview
# that is not saved and cannot be accepted.
# ASCII ONLY - PowerShell 5.1 reads .ps1 as Windows-1252 without a BOM.

param([string]$Sha, [string]$BaseUrl = "https://investwise-pro-production.up.railway.app",
      [double]$SimulateCash = 6000)

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

Write-Host "Smoke: PR 75  ($BaseUrl)" -ForegroundColor White

Sec "0  build"
$h = $null; try { $h = Invoke-RestMethod -Uri "$BaseUrl/health" -TimeoutSec 30 } catch {}
if ($null -eq $h) { Bad "/health unreachable"; exit 1 }
if ($Sha) { if ($h.commit -like "$($Sha.Trim())*") { Ok "serving $($h.commit)" } else { Bad "serving $($h.commit), expected $Sha" } } else { Skip "no -Sha" }
if ($h.schema_revision -eq '0024_plan_cash_target_ils') { Ok "schema unchanged (0024) - PR 75 has no migration" } else { Bad "schema $($h.schema_revision)" }

Sec "1  the book target keeps the planned sleeves and reports any buy-back hold"
$recs = Api GET "/api/v1/recommendations"
if ($null -eq $recs) { Bad "/recommendations did not answer"; exit 1 }
$bt = $recs.book_target
if ($bt -and ($bt.PSObject.Properties.Name -contains 'held_back') -and ($bt.PSObject.Properties.Name -contains 'planned_sleeve_targets')) {
    Ok "book target carries planned_sleeve_targets and held_back"
    foreach ($p in $bt.held_back.PSObject.Properties) { Note ("held back: {0} {1:P1} of the book, parked in cash" -f $p.Name, [double]$p.Value) }
} else { Bad "book target has no held_back / planned_sleeve_targets (old code)" }

Sec "2  a rule-vs-sleeve decision card recommends, and Sell keeps the sleeve"
$all = @(@($recs.recommendations) + @($recs.more_recommendations) | Where-Object { $_ })
$dec = @($all | Where-Object { $_.apply.kind -eq 'rule_vs_sleeve' })
if ($dec.Count -eq 0) { Skip "no rule-vs-sleeve card right now" }
else {
    foreach ($c in $dec) {
        Note $c.title
        if ($c.apply.recommended -in @('sell', 'keep')) { Ok ("recommended: {0} - {1}" -f $c.apply.recommended, $c.apply.recommended_why) } else { Bad "no recommended option" }
        $s = $c.apply.options.sell
        if ($null -ne $s.sleeve_pct -and $null -ne $s.hold_above -and -not ($s.PSObject.Properties.Name -contains 'sleeve_pct_to')) {
            Ok ("Sell keeps the {0}% sleeve; holds the buy-back while at or above {1}" -f $s.sleeve_pct, $s.hold_above)
        } else { Bad "Sell still resizes the sleeve" }
    }
}

Sec "3  cash above the trigger is researched (read-only preview at $SimulateCash)"
$sim = Api GET "/api/v1/recommendations?simulate_cash=$SimulateCash"
if ($null -eq $sim) { Bad "no preview" }
elseif (-not $sim.simulated) { Bad "?simulate_cash was ignored (old code returned the normal build)" }
else {
    Note ("cash now {0}; preview as if {1}" -f $sim.cash_now_ils, $sim.simulate_cash_ils)
    $card = @($sim.recommendations)[0]
    if ($null -eq $card) { Skip "no cash card even at $SimulateCash (nothing under target)" }
    else {
        Note $card.action
        foreach ($l in @($card.how)) { Note $l }
        if ($card.apply.kind -eq 'none') { Ok "the preview cannot be accepted" } else { Bad "preview card is actionable" }
        if ([double]$card.meta.cash_after_ils -ge 150) { Ok ("keeps {0} in cash (hard floor 150)" -f $card.meta.cash_after_ils) } else { Bad "breaches the 150 floor" }
        $rs = $card.meta.research
        if ($rs -and @($rs.records).Count -gt 0) { Ok ("researched {0} class(es); pins {1}" -f @($rs.records).Count, ($rs.assumptions -join ', ')) }
        else { Skip "no core leg to research (sleeve-only card)" }
        if (@($card.how) -match 'Not financial advice') { Ok "ends with Not financial advice" } elseif ($rs) { Bad "no disclaimer" }
    }
}

Sec "4  the page"
$page = $null; try { $page = (Invoke-WebRequest -Uri "$BaseUrl/app/" -UseBasicParsing -TimeoutSec 60).Content } catch {}
if ($null -eq $page) { Skip "could not fetch the page" }
else {
    if ($page -match 'function decisionBlock' -and $page -match 'Recommended') { Ok "decision card has option tiles and a Recommended tag" } else { Bad "old decision buttons" }
    if ($page -match 'max-height:92vh' -and $page -match 'class="hchart"') { Ok "popup is height-capped and scrolls inside" } else { Bad "old popup" }
}

Write-Host ""
Write-Host ("PASS {0}   FAIL {1}   SKIP {2}" -f $pass, $fail, $skip) -ForegroundColor White
if ($fail -gt 0) { exit 1 }
